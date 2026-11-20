from enum import StrEnum


class UnitStatus(StrEnum):
    """Lifecycle of a vehicle unit. Allowed transitions are defined in Phase 2."""

    IN_TRANSIT = "in_transit"
    IN_YARD = "in_yard"
    RESERVED = "reserved"
    SOLD = "sold"
    DELIVERED = "delivered"
