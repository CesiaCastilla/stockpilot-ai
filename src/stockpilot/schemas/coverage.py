from datetime import datetime

from pydantic import BaseModel, Field

from stockpilot.domain.coverage import CoverageStatus
from stockpilot.schemas.common import DatabaseId, Page


class TrimCoverage(BaseModel):
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str

    in_yard: int = Field(description="Available now")
    in_transit: int = Field(description="On its way: counts as available for coverage")
    reserved: int = Field(description="Promised to a customer: not available")
    recent_sales: int = Field(description="Units sold within the sales window")
    coverage_days: float | None = Field(
        description="(in_yard + in_transit) / daily sales. null without recent sales."
    )
    status: CoverageStatus

    aged_units: int = Field(description="In the yard for longer than aged_after_days")
    oldest_unit_days: int | None = Field(
        description="Days in stock of the oldest unit in the yard, since it entered the network"
    )


class DealerCoverage(BaseModel):
    dealer_id: int
    dealer_code: str
    yard_capacity: int
    stock_units: int = Field(description="In yard, in transit and reserved: places taken")

    as_of: datetime
    # The thresholds used, so the numbers can be explained without the code.
    sales_window_days: int
    low_coverage_days: int
    aged_after_days: int

    trims: list[TrimCoverage]


# --- Whole network --------------------------------------------------------------


class NetworkFilters(BaseModel):
    """Narrow the network queries to one model or one trim. Combined with AND."""

    vehicle_model_id: DatabaseId | None = None
    trim_id: DatabaseId | None = None


class Shortage(BaseModel):
    """One trim that will run out soon at one dealer."""

    dealer_id: int
    dealer_code: str
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str

    in_yard: int
    in_transit: int
    recent_sales: int
    coverage_days: float = Field(description="(in_yard + in_transit) / daily sales")

    pending_arrivals: int = Field(
        description="Units of this trim in pending transfer orders to this dealer"
    )
    units_needed: int = Field(
        description="Extra units to reach low_coverage_days, counting the pending arrivals. "
        "0: the pending orders already cover it, if they are approved."
    )
    free_places: int = Field(
        description="Places left in the yard after its stock and every pending order to it. "
        "More units than this would be rejected by the validator."
    )


class ShortageReport(BaseModel):
    as_of: datetime
    sales_window_days: int
    low_coverage_days: int
    items: list[Shortage] = Field(description="Lowest coverage first")


class AgedUnit(BaseModel):
    unit_id: int
    vin: str
    vehicle_model_id: int
    vehicle_model_name: str
    trim_id: int
    trim_name: str
    color: str
    dealer_id: int | None = Field(description="null: at the distribution center")
    dealer_code: str | None
    received_at: datetime
    days_in_stock: int = Field(description="Full days since it entered the network")


class AgedUnitPage(Page[AgedUnit]):
    as_of: datetime
    min_days: int
