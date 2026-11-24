from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.domain.unit_status import UnitStatus
from stockpilot.models.base import Base, TimestampMixin
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
    # Stored as VARCHAR + CHECK instead of a native PostgreSQL ENUM: adding a
    # status later is a plain migration, not an ALTER TYPE.
    status: Mapped[UnitStatus] = mapped_column(
        Enum(
            UnitStatus,
            name="unit_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda statuses: [s.value for s in statuses],
        ),
        index=True,
    )
    # NULL means the unit is at the central distribution center, not yet
    # assigned to a dealer. For a unit in transit it is the destination dealer.
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"), index=True)
    # When the unit entered the network's stock. Aging is measured from here
    # and does not reset on transfers: the financial cost keeps running.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    trim: Mapped[Trim] = relationship()
    dealer: Mapped[Dealer | None] = relationship()
