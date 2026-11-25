from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from stockpilot.schemas.common import DatabaseId

# PostgreSQL text cannot hold a NUL character, and a model can write one.
_NO_NUL = r"^[^\x00]*$"


class TransferProposal(BaseModel):
    """One transfer, as the agent proposes it. Untrusted until it is validated.

    There is no origin: the database knows where the unit is, so the model has
    nothing to get wrong. A malformed VIN is accepted here on purpose: it
    matches no unit, so the validator rejects it with a message.
    """

    model_config = ConfigDict(extra="forbid")

    vin: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=_NO_NUL)
    ] = Field(description="VIN of the unit to move.")
    destination_dealer_id: DatabaseId = Field(description="Id of the dealer that receives it.")
    # Same limit as the String(500) column.
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500, pattern=_NO_NUL)
    ] = Field(description="Why this transfer, for the person who decides.")
