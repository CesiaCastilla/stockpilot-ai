"""Every morning: measure the network and, if a transfer can fix a shortage, let
the agent propose transfers. People approve or reject them later, through the API.

Run it once with:       uv run python -m stockpilot.workflows.daily_detection
Every day, at 06:00:    uv run python -m stockpilot.workflows.scheduler
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from langchain_core.language_models import BaseChatModel
from sqlalchemy.orm import Session

from stockpilot.agents.checkpoints import CheckpointStore
from stockpilot.agents.llm import LlmNotConfiguredError, build_chat_model
from stockpilot.agents.tools import build_tool_context, build_tools
from stockpilot.core.config import Settings, get_settings
from stockpilot.core.database import build_engine, build_session_factory
from stockpilot.core.logging import configure_logging
from stockpilot.domain.agent_runs import AgentRunStatus, AgentRunTrigger
from stockpilot.domain.coverage import CoveragePolicy
from stockpilot.integrations.http import build_http_client
from stockpilot.models import AgentRun
from stockpilot.schemas.agent_runs import AgentRunRequest
from stockpilot.schemas.coverage import NetworkFilters
from stockpilot.services.agent_runs import AgentRunService, ModelFactory
from stockpilot.services.coverage import CoverageService, can_be_served

logger = structlog.get_logger(__name__)

NOTHING_TO_FIX = "no shortage a transfer can fix"
ANOTHER_RUN = "another agent run is in progress"


@dataclass(frozen=True)
class DetectionReport:
    """What the job found, and what it did about it."""

    shortages: int
    # Of those, the ones a transfer can fix: the only ones the agent is shown.
    can_be_served: int
    aged_units: int
    abandoned_runs: list[int] = field(default_factory=list)
    run: AgentRun | None = None
    # Why no run was started, when none was.
    skipped: str | None = None


class DailyDetection:
    """The job itself, with every dependency passed in, so tests can run it on
    the test database with a fake model."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        runs: AgentRunService,
        policy: CoveragePolicy,
        clock: Callable[[], datetime],
        *,
        abandoned_after: timedelta,
    ) -> None:
        self._session_factory = session_factory
        self._runs = runs
        self._policy = policy
        self._clock = clock
        self._abandoned_after = abandoned_after

    def __call__(self, model: ModelFactory, *, provider: str, model_name: str) -> DetectionReport:
        # First: a run a stopped process left "running" must not block today's.
        abandoned = self._runs.fail_abandoned(self._abandoned_after)
        now = self._clock()
        with self._session_factory() as session:
            coverage = CoverageService(session, self._policy)
            shortages = coverage.shortages(NetworkFilters(), now=now).items
            aged = coverage.count_aged_units(NetworkFilters(), now=now)
        found = DetectionReport(
            shortages=len(shortages),
            can_be_served=sum(map(can_be_served, shortages)),
            aged_units=aged,
            abandoned_runs=abandoned,
        )
        # Decided by code: no model call, no quota spent, on a morning without work.
        if not found.can_be_served:
            return replace(found, skipped=NOTHING_TO_FIX)
        # Two runs at once would each see the same shortages and both cover them.
        if self._runs.any_running():
            return replace(found, skipped=ANOTHER_RUN)
        run = self._runs.start(
            AgentRunRequest(),
            trigger=AgentRunTrigger.SCHEDULE,
            provider=provider,
            model=model_name,
        )
        return replace(found, run=self._runs.execute(run.id, model))


def run(
    settings: Settings, chat_model: Callable[[Settings], BaseChatModel] = build_chat_model
) -> int:
    """Exit code: 1 when there is no AI model or the run failed, so a scheduler can tell."""
    configure_logging(settings)
    # One id per run, like the API has one per request.
    with structlog.contextvars.bound_contextvars(correlation_id=uuid4().hex):
        return _run(settings, chat_model)


def _run(settings: Settings, chat_model: Callable[[Settings], BaseChatModel]) -> int:
    try:
        # Before anything else: a missing key fails every morning, not only on
        # the first one with a shortage.
        model = chat_model(settings)
    except LlmNotConfiguredError as error:
        logger.error("daily_detection_failed", error=str(error))
        return 1
    engine = build_engine(settings)
    checkpoints = CheckpointStore(settings.database_url)
    try:
        session_factory = build_session_factory(engine)
        with build_http_client(settings) as http:
            runs = AgentRunService(
                session_factory,
                build_tools(build_tool_context(settings, session_factory, http)),
                checkpointer=checkpoints.saver,
                clock=_now,
                max_model_calls=settings.agent_max_model_calls,
            )
            detection = DailyDetection(
                session_factory,
                runs,
                settings.coverage_policy(),
                _now,
                abandoned_after=timedelta(minutes=settings.agent_run_abandoned_after_minutes),
            )
            report = detection(
                lambda: model, provider=settings.llm_provider, model_name=settings.llm_model
            )
    finally:
        checkpoints.close()
        engine.dispose()
    _log(report)
    return 1 if report.run is not None and report.run.status is AgentRunStatus.FAILED else 0


def _log(report: DetectionReport) -> None:
    run = report.run
    logger.info(
        "daily_detection_done",
        shortages=report.shortages,
        can_be_served=report.can_be_served,
        aged_units=report.aged_units,
        abandoned_runs=report.abandoned_runs,
        skipped=report.skipped,
        agent_run_id=run.id if run else None,
        status=run.status.value if run else None,
        outcome=run.outcome.value if run and run.outcome else None,
        orders=sum("order_id" in s for s in run.submissions) if run else 0,
    )


def _now() -> datetime:
    return datetime.now(UTC)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run(get_settings()))
