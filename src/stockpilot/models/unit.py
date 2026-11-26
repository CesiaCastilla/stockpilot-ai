from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models.base import Base, TimestampMixin, enum_type
from stockpilot.models.catalog import Trim
from stockpilot.models.dealer import Dealer


class Unit(TimestampMixin, Base):
    """A physical vehicle, identified by its VIN."""

    __tablename__ = "units"
    # Full VIN validation (charset, check digit) lives in the domain layer;
    # the database only guards the cheapest invariant.
    __table_args__ = (CheckConstraint("char_length(vin) = 17", name="vin_length"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True)
    trim_id: Mapped[int] = mapped_column(ForeignKey("trims.id"), index=True)
    color: Mapped[str] = mapped_column(String(40))
    status: Mapped[UnitStatus] = mapped_column(
        enum_type(UnitStatus, name="unit_status"), index=True
    )
    # NULL means the unit is at the central distribution center, not yet
    # assigned to a dealer. For a unit in transit it is the destination dealer.
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"), index=True)
    # When the unit entered the network's stock. Aging is measured from here
    # and does not reset on transfers: the financial cost keeps running.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    trim: Mapped[Trim] = relationship()
    dealer: Mapped[Dealer | None] = relationship()


class UnitEvent(Base):
    """One status change of a unit. Append-only: events are never updated or deleted.

    With the status machine, from/to status plus the dealer after the change
    tell the whole story: registration (from NULL), arrival, reservation,
    sale, and transfers (in_yard -> in_transit with a new dealer).
    """

    __tablename__ = "unit_events"
    __table_args__ = (
        # The history of a unit is always read in order: this index serves it.
        Index("ix_unit_events_unit_id_occurred_at", "unit_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    # NULL only for the registration event.
    from_status: Mapped[UnitStatus | None] = mapped_column(
        enum_type(UnitStatus, name="from_status")
    )
    to_status: Mapped[UnitStatus] = mapped_column(enum_type(UnitStatus, name="to_status"))
    # Where the unit is after the change (NULL: distribution center).
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"))
    # Set by the service; the server default is only a safety net.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    unit: Mapped[Unit] = relationship()
    dealer: Mapped[Dealer | None] = relationship()
