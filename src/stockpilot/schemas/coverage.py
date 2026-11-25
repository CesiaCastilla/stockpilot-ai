from datetime import datetime

from pydantic import BaseModel, Field

from stockpilot.domain.coverage import CoverageStatus


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
