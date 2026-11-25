"""Catalog schemas: vehicle models and their trims."""

from decimal import Decimal
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# Same limit as the String(80) columns, so the database never truncates or rejects.
CatalogName = Annotated[str, Field(min_length=1, max_length=80)]
# Same size as the Numeric(10, 2) column. Always two decimals, so the API answers
# the same text before and after the price goes through the database.
ListPrice = Annotated[
    Decimal,
    Field(gt=0, max_digits=10, decimal_places=2, examples=["25000.00"]),
    AfterValidator(lambda price: price.quantize(Decimal("0.01"))),
]


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
    list_price_usd: ListPrice | None = None


class TrimUpdate(BaseModel):
    """Partial update: only the fields sent are changed. Moving a trim to another
    model would silently change the model of every unit already built with it."""

    model_config = ConfigDict(extra="forbid")

    name: CatalogName | None = None
    list_price_usd: ListPrice | None = Field(
        default=None, description="null clears it: the trim has no price."
    )

    @model_validator(mode="after")
    def _name_cannot_be_null(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class TrimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_model_id: int
    name: str
    list_price_usd: Decimal | None
