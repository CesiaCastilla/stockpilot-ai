from enum import StrEnum

from stockpilot.domain.errors import InvalidStatusTransitionError


class UnitStatus(StrEnum):
    """Lifecycle of a vehicle unit."""

    IN_TRANSIT = "in_transit"
    IN_YARD = "in_yard"
    RESERVED = "reserved"
    SOLD = "sold"
    DELIVERED = "delivered"


# Every status change not listed here is a business error.
ALLOWED_TRANSITIONS: dict[UnitStatus, frozenset[UnitStatus]] = {
    # Arrives at its destination yard.
    UnitStatus.IN_TRANSIT: frozenset({UnitStatus.IN_YARD}),
    # Reserved by a customer, sold on the spot, or shipped to another dealer.
    UnitStatus.IN_YARD: frozenset({UnitStatus.RESERVED, UnitStatus.SOLD, UnitStatus.IN_TRANSIT}),
    # The reservation is cancelled or becomes a sale. A reserved unit is never
    # transferred: it is promised to a customer at that dealer.
    UnitStatus.RESERVED: frozenset({UnitStatus.IN_YARD, UnitStatus.SOLD}),
    UnitStatus.SOLD: frozenset({UnitStatus.DELIVERED}),
    UnitStatus.DELIVERED: frozenset(),
}


def ensure_transition(current: UnitStatus, target: UnitStatus) -> None:
    """Raise InvalidStatusTransitionError unless current -> target is allowed."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransitionError(current.value, target.value)
