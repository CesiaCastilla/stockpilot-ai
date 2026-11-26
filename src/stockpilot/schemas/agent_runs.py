from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.schemas.common import DatabaseId

MAX_PROPOSALS_PER_RUN = 10


class AgentRunRequest(BaseModel):
    """What to look at. No free text: the prompt is built from these fields, so
    nobody can slip instructions to the model through the API."""

    model_config = ConfigDict(extra="forbid")

    vehicle_model_id: DatabaseId | None = Field(default=None, description="Only this model")
    trim_id: DatabaseId | None = Field(default=None, description="Only this trim")
    max_proposals: int = Field(
        default=5, ge=1, le=MAX_PROPOSALS_PER_RUN, description="At most this many transfers"
    )


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: AgentRunStatus
    outcome: AgentRunOutcome | None
    trigger: AgentRunTrigger
    request: dict[str, Any]
    llm_provider: str
    llm_model: str
    tool_calls: list[dict[str, Any]] = Field(description="Every tool the model asked for, in order")
    submissions: list[dict[str, Any]] = Field(
        description="Every proposal, with the validator's answer and the order it created"
    )
    summary: str | None
    error: str | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    created_at: datetime
    finished_at: datetime | None
