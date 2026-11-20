"""Product catalog: vehicle models and their trims (versiones)."""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stockpilot.models.base import Base, TimestampMixin


class VehicleModel(TimestampMixin, Base):
    __tablename__ = "vehicle_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Trim(TimestampMixin, Base):
    __tablename__ = "trims"
    # The same trim name can exist in different models, never twice in one model.
    __table_args__ = (UniqueConstraint("vehicle_model_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))

    vehicle_model: Mapped[VehicleModel] = relationship()
