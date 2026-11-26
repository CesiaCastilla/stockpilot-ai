"""States of an agent run. Pure: no database, no AI."""

from enum import StrEnum


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    # An error stopped it: the model or a tool failed. Orders created before stay pending.
    FAILED = "failed"


class AgentRunOutcome(StrEnum):
    """How a completed run ended."""

    # At least one proposal passed the validator and is a pending order now.
    PROPOSED = "proposed"
    # The model found nothing worth moving.
    NO_PROPOSAL = "no_proposal"
    # Every proposal was rejected, even after the model corrected them once.
    REJECTED = "rejected"
    # It kept asking for data and never proposed: stopped to cap the cost.
    STEP_LIMIT = "step_limit"


class AgentRunTrigger(StrEnum):
    API = "api"
    SCHEDULE = "schedule"
