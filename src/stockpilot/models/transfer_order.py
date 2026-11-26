from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models.base import Base, TimestampMixin, enum_type
from stockpilot.models.dealer import Dealer
from stockpilot.models.unit import Unit


class TransferOrder(TimestampMixin, Base):
    """A proposed move of one unit to a dealer. Nothing moves until a person approves it."""

    __tablename__ = "transfer_orders"
    __table_args__ = (
        # A NULL origin passes: the distribution center is never the destination.
        CheckConstraint("origin_dealer_id <> destination_dealer_id", name="different_dealers"),
        # A closed order always says when, and a rejected one also why.
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="closed_has_date"),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)", name="rejected_has_reason"
        ),
        # A unit can have many closed orders, but only one waiting for a decision.
        Index(
            "uq_transfer_orders_pending_unit_id",
            "unit_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    # Where the unit was when the order was proposed (NULL: distribution
    # center). It can move before the approval, so it is stored, not derived.
    origin_dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"))
    destination_dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), index=True)
    status: Mapped[TransferOrderStatus] = mapped_column(
        enum_type(TransferOrderStatus, name="status"), index=True
    )
    # Why it was proposed: what the person who decides reads.
    reason: Mapped[str] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))

    unit: Mapped[Unit] = relationship()
    origin_dealer: Mapped[Dealer | None] = relationship(foreign_keys=[origin_dealer_id])
    destination_dealer: Mapped[Dealer] = relationship(foreign_keys=[destination_dealer_id])
