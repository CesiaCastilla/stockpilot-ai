"""Catalog schemas: vehicle models and their trims."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# Same limit as the String(80) columns, so the database never truncates or rejects.
CatalogName = Annotated[str, Field(min_length=1, max_length=80)]


class VehicleModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CatalogName = Field(examples=["Pickup X"])


class VehicleModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CatalogName


class VehicleModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TrimCreate(BaseModel):
    """The vehicle model comes from the URL, not from the body."""

    model_config = ConfigDict(extra="forbid")

    name: CatalogName = Field(examples=["Base"])


class TrimUpdate(BaseModel):
    """Only the name changes. Moving a trim to another model would silently
    change the model of every unit already built with it."""

    model_config = ConfigDict(extra="forbid")

    name: CatalogName


class TrimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_model_id: int
    name: str
