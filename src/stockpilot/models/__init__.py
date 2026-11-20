"""ORM models. Importing this package registers every table on Base.metadata (used by Alembic)."""

from stockpilot.models.base import Base
from stockpilot.models.catalog import Trim, VehicleModel
from stockpilot.models.dealer import Dealer
from stockpilot.models.unit import Unit

__all__ = ["Base", "Dealer", "Trim", "Unit", "VehicleModel"]
