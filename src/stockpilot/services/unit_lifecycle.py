from datetime import UTC, datetime

from sqlalchemy.orm import Session

from stockpilot.domain.errors import InvalidStatusTransitionError
from stockpilot.domain.unit_status import UnitStatus, ensure_transition
from stockpilot.models import Unit, UnitEvent
from stockpilot.repositories.units import UnitRepository


class UnitLifecycle:
    """The only place that changes the status of a unit, so every change leaves
    a UnitEvent behind.

    It never commits: a status change is always part of a larger operation
    (a sale, the approval of a transfer) that is one transaction.
    """

    def __init__(self, session: Session) -> None:
        self.units = UnitRepository(session)

    def change(
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

    def ship(self, unit: Unit, destination_id: int) -> datetime:
        """The unit leaves for another dealer. From now on it counts in that yard."""
        unit.dealer_id = destination_id  # before the event, which stores it
        return self.change(unit, UnitStatus.IN_TRANSIT)

    def record_registration(self, unit: Unit, *, now: datetime) -> None:
        self._record(unit, from_status=None, now=now)

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
