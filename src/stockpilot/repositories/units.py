from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from stockpilot.domain.errors import AlreadyExistsError
from stockpilot.models import Trim, Unit, UnitEvent
from stockpilot.repositories.errors import translate_violations
from stockpilot.schemas.units import UnitFilters


class UnitRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, unit_id: int) -> Unit | None:
        return self.session.get(Unit, unit_id)

    def get_by_vin(self, vin: str) -> Unit | None:
        return self.session.scalar(select(Unit).where(Unit.vin == vin))

    def search(self, filters: UnitFilters, *, limit: int, offset: int) -> Sequence[Unit]:
        # Oldest first: the units that have waited longest are the ones to act on.
        query = _filtered(select(Unit), filters).order_by(Unit.received_at, Unit.id)
        return self.session.scalars(query.limit(limit).offset(offset)).all()

    def count(self, filters: UnitFilters) -> int:
        query = _filtered(select(func.count()).select_from(Unit), filters)
        return self.session.scalar(query) or 0

    def add(self, unit: Unit) -> Unit:
        duplicate = AlreadyExistsError("Unit", "VIN", unit.vin)
        with translate_violations(self.session, {"uq_units_vin": duplicate}):
            self.session.add(unit)
        return unit

    # --- History ----------------------------------------------------------------

    def add_event(self, event: UnitEvent) -> UnitEvent:
        self.session.add(event)
        return event

    def events_for(self, unit_id: int) -> Sequence[UnitEvent]:
        # Events of one transaction share occurred_at; the id breaks the tie.
        query = (
            select(UnitEvent)
            .where(UnitEvent.unit_id == unit_id)
            .order_by(UnitEvent.occurred_at, UnitEvent.id)
        )
        return self.session.scalars(query).all()


def _filtered[Q: Select[Any]](query: Q, filters: UnitFilters) -> Q:
    """Apply the same WHERE clauses to the page query and to its count."""
    if filters.status:
        query = query.where(Unit.status.in_(filters.status))
    if filters.dealer_id is not None:
        query = query.where(Unit.dealer_id == filters.dealer_id)
    if filters.at_distribution_center is True:
        query = query.where(Unit.dealer_id.is_(None))
    elif filters.at_distribution_center is False:
        query = query.where(Unit.dealer_id.is_not(None))
    if filters.trim_id is not None:
        query = query.where(Unit.trim_id == filters.trim_id)
    if filters.vehicle_model_id is not None:
        trims_of_model = select(Trim.id).where(Trim.vehicle_model_id == filters.vehicle_model_id)
        query = query.where(Unit.trim_id.in_(trims_of_model))
    return query
