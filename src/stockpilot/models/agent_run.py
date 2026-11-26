from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.models.base import Base, TimestampMixin, enum_type


class AgentRun(TimestampMixin, Base):
    """Audit of one agent run: what it was asked, what it looked at, what it
    proposed and what the validator said. The human decision is on the orders
    it created (transfer_orders.agent_run_id)."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_has_date"),
        CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name="failed_has_error"),
        CheckConstraint(
            "(status = 'completed') = (outcome IS NOT NULL)", name="completed_has_outcome"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_type(AgentRunStatus, name="status"), index=True
    )
    outcome: Mapped[AgentRunOutcome | None] = mapped_column(
        enum_type(AgentRunOutcome, name="outcome")
    )
    trigger: Mapped[AgentRunTrigger] = mapped_column(enum_type(AgentRunTrigger, name="trigger"))
    # The request as it was received, to run it again or explain it.
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    llm_provider: Mapped[str] = mapped_column(String(40))
    llm_model: Mapped[str] = mapped_column(String(100))

    # JSONB: a list whose items differ by tool. Read whole, never queried inside.
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Every proposal the model submitted, with the validator's answer.
    submissions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # The model's last words, when it finished by answering instead of proposing.
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    model_calls: Mapped[int] = mapped_column(default=0)
    # What the run cost, in the unit the provider's quota is measured in.
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
