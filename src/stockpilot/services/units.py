from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, InvalidStatusTransitionError, NotFoundError
from stockpilot.domain.unit_status import UnitStatus, ensure_transition
from stockpilot.domain.vin import parse_vin
from stockpilot.models import Dealer, Reservation, Sale, Unit, UnitEvent
from stockpilot.repositories.catalog import TrimRepository
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.sales import SalesRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.units import UnitCreate, UnitFilters


class UnitService:
    """Unit registration, lifecycle and queries. Each public method is one transaction.

    Every status change of a unit goes through this service, so it always
    leaves a UnitEvent behind.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.units = UnitRepository(session)
        self.trims = TrimRepository(session)
        self.dealers = DealerRepository(session)
        self.sales = SalesRepository(session)

    def get(self, unit_id: int) -> Unit:
        unit = self.units.get(unit_id)
        if unit is None:
            raise NotFoundError("Unit", unit_id)
        return unit

    def get_by_vin(self, raw_vin: str) -> Unit:
        vin = raw_vin.strip().upper()
        unit = self.units.get_by_vin(vin)
        if unit is None:
            raise NotFoundError("Unit", vin)
        return unit

    def search(
        self, filters: UnitFilters, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[Unit], int]:
        return self.units.search(filters, limit=limit, offset=offset), self.units.count(filters)

    def history(self, raw_vin: str) -> Sequence[UnitEvent]:
        unit = self.get_by_vin(raw_vin)  # 404 for an unknown unit, not an empty history
        return self.units.events_for(unit.id)

    def register(self, data: UnitCreate) -> Unit:
        """A new unit enters the network's stock.

        Without a dealer it waits in the distribution center yard; with one it
        is shipped there right away, so it already takes a place in that yard.
        """
        # Domain rule, enforced here so every entry point (API, agent, jobs) gets it.
        vin = parse_vin(data.vin)

        trim = self.trims.get(data.trim_id)
        if trim is None:
            raise NotFoundError("Trim", data.trim_id)

        dealer: Dealer | None = None
        status = UnitStatus.IN_YARD
        if data.dealer_id is not None:
            dealer = self._dealer_with_room(data.dealer_id)
            status = UnitStatus.IN_TRANSIT

        now = datetime.now(UTC)
        unit = self.units.add(
            Unit(
                vin=vin, trim=trim, color=data.color, status=status, dealer=dealer, received_at=now
            )
        )
        self._record(unit, from_status=None, now=now)
        self.session.commit()
        return unit

    # --- Lifecycle ------------------------------------------------------------

    def arrive(self, unit_id: int) -> Unit:
        """The unit reaches its destination yard. received_at does not change:
        aging counts since it entered the network."""
        unit = self._locked(unit_id)
        self._change_status(unit, UnitStatus.IN_YARD, only_from=UnitStatus.IN_TRANSIT)
        self.session.commit()
        return unit

    def reserve(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        dealer_id = self._selling_dealer(unit)  # every check runs before any change
        now = self._change_status(unit, UnitStatus.RESERVED)
        self.sales.add_reservation(
            Reservation(unit_id=unit.id, dealer_id=dealer_id, reserved_at=now)
        )
        self.session.commit()
        return unit

    def cancel_reservation(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        now = self._change_status(unit, UnitStatus.IN_YARD, only_from=UnitStatus.RESERVED)
        # Closed, not deleted: the reservation stays in the history.
        self.sales.active_reservation(unit.id).cancelled_at = now
        self.session.commit()
        return unit

    def sell(self, unit_id: int) -> Unit:
        """Sold on the spot or after a reservation. A fulfilled reservation is
        not cancelled: cancelled_at stays NULL."""
        unit = self._locked(unit_id)
        dealer_id = self._selling_dealer(unit)
        now = self._change_status(unit, UnitStatus.SOLD)
        self.sales.add_sale(Sale(unit_id=unit.id, dealer_id=dealer_id, sold_at=now))
        self.session.commit()
        return unit

    def deliver(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        self._change_status(unit, UnitStatus.DELIVERED)
        self.session.commit()
        return unit

    # --- Helpers --------------------------------------------------------------

    def _locked(self, unit_id: int) -> Unit:
        unit = self.units.get_for_update(unit_id)
        if unit is None:
            raise NotFoundError("Unit", unit_id)
        return unit

    def _change_status(
        self, unit: Unit, target: UnitStatus, *, only_from: UnitStatus | None = None
    ) -> datetime:
        """Apply a transition, record its event and return when it happened.

        only_from narrows the status machine when two operations share a
        target: "arrive" and "cancel reservation" both end in the yard, and
        arriving must not release a reserved unit.
        """
        if only_from is not None and unit.status is not only_from:
            raise InvalidStatusTransitionError(unit.status.value, target.value)
        ensure_transition(unit.status, target)

        now = datetime.now(UTC)
        previous = unit.status
        unit.status = target
        self._record(unit, from_status=previous, now=now)
        return now

    def _record(self, unit: Unit, *, from_status: UnitStatus | None, now: datetime) -> None:
        self.units.add_event(
            UnitEvent(
                unit_id=unit.id,
                from_status=from_status,
                to_status=unit.status,
                dealer_id=unit.dealer_id,
                occurred_at=now,
            )
        )

    @staticmethod
    def _selling_dealer(unit: Unit) -> int:
        if unit.dealer_id is None:
            raise ConflictError(
                f"Unit {unit.vin} is at the distribution center: "
                "only units at a dealer can be reserved or sold"
            )
        return unit.dealer_id

    def _dealer_with_room(self, dealer_id: int) -> Dealer:
        # Locked: two concurrent registrations cannot both take the last place.
        dealer = self.dealers.get_for_update(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)
        # In-transit units count against the destination yard (STOCK_STATUSES).
        stock = self.dealers.count_stock_units(dealer_id)
        if stock >= dealer.yard_capacity:
            raise ConflictError(
                f"Dealer {dealer.code} yard is full ({stock} of {dealer.yard_capacity} units)"
            )
        return dealer
