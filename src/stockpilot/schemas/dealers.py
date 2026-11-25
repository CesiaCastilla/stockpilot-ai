from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from stockpilot.schemas.common import MAX_INTEGER, NoNul

DealerCode = Annotated[str, Field(pattern=r"^[A-Z0-9]{2,10}$", examples=["D001"])]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), NoNul]
Province = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    NoNul,
    Field(examples=["Córdoba"]),
]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
YardCapacity = Annotated[
    int, Field(gt=0, le=MAX_INTEGER, description="Max units the dealer yard can hold")
]


class DealerCreate(BaseModel):
    # Unknown fields are rejected: a typo should fail loudly, not be ignored.
    model_config = ConfigDict(extra="forbid")

    code: DealerCode
    name: Name
    province: Province
    city: Name
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    yard_capacity: YardCapacity


class DealerUpdate(BaseModel):
    """Partial update: only the fields sent are changed. The code is immutable."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    province: Province | None = None
    city: Name | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    yard_capacity: YardCapacity | None = None

    @model_validator(mode="after")
    def _required_fields_cannot_be_null(self) -> Self:
        # Coordinates can be cleared with null; these columns cannot be empty.
        for field in ("name", "province", "city", "yard_capacity"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class DealerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    province: str
    city: str
    latitude: float | None
    longitude: float | None
    yard_capacity: int
