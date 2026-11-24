from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.domain.unit_status import UnitStatus
from stockpilot.domain.vin import parse_vin
from stockpilot.models import Dealer, Unit, UnitEvent
from stockpilot.repositories.catalog import TrimRepository
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.repositories.units import UnitRepository
from stockpilot.schemas.units import UnitCreate, UnitFilters


class UnitService:
    """Unit registration and queries. Each public method is one transaction.

    Every status change of a unit goes through this service, so it always
    leaves a UnitEvent behind.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.units = UnitRepository(session)
        self.trims = TrimRepository(session)
        self.dealers = DealerRepository(session)

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

    def history(self, unit_id: int) -> Sequence[UnitEvent]:
        self.get(unit_id)  # 404 for an unknown unit, not an empty history
        return self.units.events_for(unit_id)

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
        self.units.add_event(
            UnitEvent(
                unit_id=unit.id,
                from_status=None,
                to_status=status,
                dealer_id=unit.dealer_id,
                occurred_at=now,
            )
        )
        self.session.commit()
        return unit

    def _dealer_with_room(self, dealer_id: int) -> Dealer:
        dealer = self.dealers.get(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)
        # In-transit units count against the destination yard (STOCK_STATUSES).
        stock = self.dealers.count_stock_units(dealer_id)
        if stock >= dealer.yard_capacity:
            raise ConflictError(
                f"Dealer {dealer.code} yard is full ({stock} of {dealer.yard_capacity} units)"
            )
        return dealer
