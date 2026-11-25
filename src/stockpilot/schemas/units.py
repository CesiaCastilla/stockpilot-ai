from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from stockpilot.domain.unit_status import UnitStatus

# Trimmed everywhere, so the filter always finds what was stored.
# Same limit as the String(40) column.
Color = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
    Field(examples=["White"]),
]


class UnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vin: str = Field(
        examples=["8AFZZZ12345670001"],
        description="17 characters, no I, O or Q. Normalized to upper case. "
        "VINs starting with 1-5 (North America) must have a valid check digit.",
    )
    trim_id: int
    color: Color
    dealer_id: int | None = Field(
        default=None,
        description="Destination dealer: the unit is registered in transit to it. "
        "Omit it to register the unit in the distribution center yard.",
    )


class UnitUpdate(BaseModel):
    """Partial update to correct data entry errors: only the fields sent are changed.

    The VIN identifies the unit, and its status and dealer only change through
    the lifecycle operations, which apply their rules and leave an event.
    """

    model_config = ConfigDict(extra="forbid")

    trim_id: int | None = None
    color: Color | None = None

    @model_validator(mode="after")
    def _fields_cannot_be_null(self) -> Self:
        # None means "not sent"; an explicit null is an error, not a no-op.
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


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
    color: Color | None = Field(
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
