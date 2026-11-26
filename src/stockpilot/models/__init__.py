"""ORM models. Importing this package registers every table on Base.metadata (used by Alembic)."""

from stockpilot.models.agent_run import AgentRun
from stockpilot.models.base import Base
from stockpilot.models.catalog import Trim, VehicleModel
from stockpilot.models.dealer import Dealer
from stockpilot.models.sales import Reservation, Sale
from stockpilot.models.transfer_order import TransferOrder
from stockpilot.models.unit import Unit, UnitEvent

__all__ = [
    "AgentRun",
    "Base",
    "Dealer",
    "Reservation",
    "Sale",
    "TransferOrder",
    "Trim",
    "Unit",
    "UnitEvent",
    "VehicleModel",
]
