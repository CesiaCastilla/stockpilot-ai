"""Commercial events on a unit: reservations and sales."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.models.base import Base, TimestampMixin
from stockpilot.models.dealer import Dealer
from stockpilot.models.unit import Unit


class Reservation(TimestampMixin, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "cancelled_at IS NULL OR cancelled_at >= reserved_at",
            name="cancelled_after_reserved",
        ),
        # A unit can have many cancelled reservations in its history, but only
        # one that is still active. A partial unique index enforces exactly that.
        Index(
            "uq_reservations_active_unit_id",
            "unit_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), index=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    unit: Mapped[Unit] = relationship()
    dealer: Mapped[Dealer] = relationship()


class Sale(TimestampMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (
        # Coverage asks "sales of dealer X since date Y": this index serves it.
        Index("ix_sales_dealer_id_sold_at", "dealer_id", "sold_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # A unit is sold once.
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), unique=True)
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"))
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    unit: Mapped[Unit] = relationship()
    dealer: Mapped[Dealer] = relationship()
