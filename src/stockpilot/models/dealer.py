from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from stockpilot.models.base import Base, TimestampMixin


class Dealer(TimestampMixin, Base):
    __tablename__ = "dealers"
    __table_args__ = (CheckConstraint("yard_capacity > 0", name="yard_capacity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    province: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(120))
    # Filled later by the Georef sync; nullable until then.
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    # Max units the dealer yard can hold: the validator rejects transfers above it.
    yard_capacity: Mapped[int]
