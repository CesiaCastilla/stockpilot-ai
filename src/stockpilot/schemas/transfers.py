from datetime import datetime
from typing import Annotated

from pydantic import AliasPath, BaseModel, ConfigDict, Field, StringConstraints

from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.schemas.common import DatabaseId, NoNul

# Same limit as the String(500) columns.
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500), NoNul
]


class TransferProposal(BaseModel):
    """One transfer, as the agent proposes it. Untrusted until it is validated.

    There is no origin: the database knows where the unit is, so the model has
    nothing to get wrong. A malformed VIN is accepted here on purpose: it
    matches no unit, so the validator rejects it with a message.
    """

    model_config = ConfigDict(extra="forbid")

    vin: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True), NoNul] = Field(
        description="VIN of the unit to move."
    )
    destination_dealer_id: DatabaseId = Field(description="Id of the dealer that receives it.")
    reason: Reason = Field(description="Why this transfer, for the person who decides.")


class TransferRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason = Field(examples=["The unit is promised to a customer"])


class TransferOrderRead(BaseModel):
    """Includes the VIN and the dealer codes: what a person needs to decide."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    unit_id: int
    vin: str = Field(validation_alias=AliasPath("unit", "vin"))
    origin_dealer_id: int | None
    origin_dealer_code: str | None = Field(
        validation_alias=AliasPath("origin_dealer", "code"),
        default=None,
        description="null: the distribution center.",
    )
    destination_dealer_id: int
    destination_dealer_code: str = Field(validation_alias=AliasPath("destination_dealer", "code"))
    status: TransferOrderStatus
    reason: str
    created_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None
    agent_run_id: int | None = Field(description="The agent run that proposed it")
