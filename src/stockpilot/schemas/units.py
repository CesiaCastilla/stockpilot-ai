from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from stockpilot.domain.unit_status import UnitStatus


class UnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vin: str = Field(
        examples=["8AFZZZ12345670001"],
        description="17 characters, no I, O or Q. Normalized to upper case. "
        "VINs starting with 1-5 (North America) must have a valid check digit.",
    )
    trim_id: int
    color: Annotated[str, Field(min_length=1, max_length=40, examples=["White"])]
    dealer_id: int | None = Field(
        default=None,
        description="Destination dealer: the unit is registered in transit to it. "
        "Omit it to register the unit in the distribution center yard.",
    )


class UnitFilters(BaseModel):
    """Query filters for the unit list. All of them are combined with AND."""

    status: list[UnitStatus] = Field(
        default_factory=list, description="Repeat to match any of several statuses."
    )
    dealer_id: int | None = None
    at_distribution_center: bool | None = Field(
        default=None, description="true: only units without a dealer; false: only with one."
    )
    trim_id: int | None = None
    vehicle_model_id: int | None = None
    color: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = Field(
        default=None, description="Exact color, ignoring upper and lower case."
    )


class UnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vin: str
    trim_id: int
    color: str
    status: UnitStatus
    dealer_id: int | None
    received_at: datetime


class UnitEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: UnitStatus | None
    to_status: UnitStatus
    dealer_id: int | None
    occurred_at: datetime
