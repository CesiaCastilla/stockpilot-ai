from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.domain.unit_status import STOCK_STATUSES, UnitStatus
from stockpilot.domain.vin import parse_vin
from stockpilot.models import Dealer, Reservation, Sale, Unit, UnitEvent
from stockpilot.repositories.catalog import TrimRepository
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.sales import SalesRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.units import UnitCreate, UnitFilters, UnitUpdate
from stockpilot.services.unit_lifecycle import UnitLifecycle


class UnitService:
    """Unit registration, lifecycle and queries. Each public method is one transaction.

    Status changes go through UnitLifecycle, so they always leave a UnitEvent behind.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.units = UnitRepository(session)
        self.trims = TrimRepository(session)
        self.dealers = DealerRepository(session)
        self.sales = SalesRepository(session)
        self.lifecycle = UnitLifecycle(session)

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
        self.lifecycle.record_registration(unit, now=now)
        self.session.commit()
        return unit

    def update(self, unit_id: int, data: UnitUpdate) -> Unit:
        """Correct the trim or the color of a unit that is still in stock.

        Once sold it is frozen: changing its trim would rewrite the past sales
        that coverage is computed from.
        """
        # Locked: the unit cannot be sold between the check and the commit.
        unit = self._locked(unit_id)
        if unit.status not in STOCK_STATUSES:
            raise ConflictError(
                f"Unit {unit.vin} is {unit.status.value}: only units in stock can be corrected"
            )
        if data.trim_id is not None:
            trim = self.trims.get(data.trim_id)
            if trim is None:
                raise NotFoundError("Trim", data.trim_id)
            unit.trim = trim
        if data.color is not None:
            unit.color = data.color
        self.session.commit()
        return unit

    # --- Lifecycle ------------------------------------------------------------

    def arrive(self, unit_id: int) -> Unit:
        """The unit reaches its destination yard. received_at does not change:
        aging counts since it entered the network."""
        unit = self._locked(unit_id)
        self.lifecycle.change(unit, UnitStatus.IN_YARD, only_from=UnitStatus.IN_TRANSIT)
        self.session.commit()
        return unit

    def reserve(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        dealer_id = self._selling_dealer(unit)  # every check runs before any change
        now = self.lifecycle.change(unit, UnitStatus.RESERVED)
        self.sales.add_reservation(
            Reservation(unit_id=unit.id, dealer_id=dealer_id, reserved_at=now)
        )
        self.session.commit()
        return unit

    def cancel_reservation(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        now = self.lifecycle.change(unit, UnitStatus.IN_YARD, only_from=UnitStatus.RESERVED)
        # Closed, not deleted: the reservation stays in the history.
        self.sales.active_reservation(unit.id).cancelled_at = now
        self.session.commit()
        return unit

    def sell(self, unit_id: int) -> Unit:
        """Sold on the spot or after a reservation. A fulfilled reservation is
        not cancelled: cancelled_at stays NULL."""
        unit = self._locked(unit_id)
        dealer_id = self._selling_dealer(unit)
        now = self.lifecycle.change(unit, UnitStatus.SOLD)
        self.sales.add_sale(Sale(unit_id=unit.id, dealer_id=dealer_id, sold_at=now))
        self.session.commit()
        return unit

    def deliver(self, unit_id: int) -> Unit:
        unit = self._locked(unit_id)
        self.lifecycle.change(unit, UnitStatus.DELIVERED)
        self.session.commit()
        return unit

    # --- Helpers --------------------------------------------------------------

    def _locked(self, unit_id: int) -> Unit:
        unit = self.units.get_for_update(unit_id)
        if unit is None:
            raise NotFoundError("Unit", unit_id)
        return unit

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
