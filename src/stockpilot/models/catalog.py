"""Product catalog: vehicle models and their trims (versiones)."""

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.models.base import Base, TimestampMixin


class VehicleModel(TimestampMixin, Base):
    __tablename__ = "vehicle_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Trim(TimestampMixin, Base):
    __tablename__ = "trims"
    # The same trim name can exist in different models, never twice in one model.
    __table_args__ = (
        UniqueConstraint("vehicle_model_id", "name"),
        CheckConstraint("list_price_usd > 0", name="list_price_usd_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # NULL: not loaded yet. Numeric, never float: it is money.
    list_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    vehicle_model: Mapped[VehicleModel] = relationship()
