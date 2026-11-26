from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, cast

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session

from stockpilot.agents.graph import AgentLimits, AgentState, build_graph, initial_state
from stockpilot.agents.prompts import system_prompt, task
from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.domain.errors import NotFoundError
from stockpilot.models import AgentRun
from stockpilot.repositories.agent_runs import AgentRunRepository
from stockpilot.schemas.agent_runs import AgentRunRequest
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.transfers import TransferService

logger = structlog.get_logger(__name__)

# The error is stored for people to read, not to keep a whole traceback.
MAX_ERROR_LENGTH = 2000


class AgentRunService:
    """Starts agent runs and records what each one did.

    A run lasts as long as the model takes, so it does not hold one session:
    each write opens its own, and so does each tool call.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        tools: Sequence[BaseTool],
        *,
        clock: Callable[[], datetime],
        max_model_calls: int,
    ) -> None:
        self._session_factory = session_factory
        self._tools = tools
        self._clock = clock
        self._max_model_calls = max_model_calls

    def start(
        self, request: AgentRunRequest, *, trigger: AgentRunTrigger, provider: str, model: str
    ) -> AgentRun:
        with self._session_factory() as session:
            run = AgentRunRepository(session).add(
                AgentRun(
                    status=AgentRunStatus.RUNNING,
                    trigger=trigger,
                    request=request.model_dump(mode="json"),
                    llm_provider=provider,
                    llm_model=model,
                    tool_calls=[],
                    submissions=[],
                )
            )
            session.commit()
            return run

    def execute(self, run_id: int, model: BaseChatModel) -> AgentRun:
        """Run the agent to the end and record it. Never raises: a failure is a
        failed run, so a background task or a job always leaves a record."""
        run = self.get(run_id)
        request = AgentRunRequest.model_validate(run.request)

        def propose(proposal: TransferProposal) -> int:
            with self._session_factory() as session:
                return TransferService(session).propose(proposal, agent_run_id=run_id).id

        graph = build_graph(
            model,
            self._tools,
            propose,
            system_prompt=system_prompt(self._clock().date()),
            limits=AgentLimits(
                max_proposals=request.max_proposals, max_model_calls=self._max_model_calls
            ),
        )
        state = initial_state(task(request))
        config: RunnableConfig = {"recursion_limit": 4 * self._max_model_calls}
        log = logger.bind(agent_run_id=run_id)
        log.info("agent_run_started", request=run.request)
        try:
            # Step by step, so a failure halfway still records what happened before it.
            for step in graph.stream(initial_state(task(request)), config, stream_mode="values"):
                # stream_mode="values" yields the whole state after each step.
                state = cast(AgentState, step)
        except Exception as error:  # the model, the network, a bug: all end the same way
            log.exception("agent_run_failed")
            return self._finish(run_id, state, error=f"{type(error).__name__}: {error}")
        log.info("agent_run_finished", stop=state["stop"], accepted=state["accepted"])
        return self._finish(run_id, state)

    def get(self, run_id: int) -> AgentRun:
        with self._session_factory() as session:
            run = AgentRunRepository(session).get(run_id)
        if run is None:
            raise NotFoundError("Agent run", run_id)
        return run

    def search(self, *, limit: int = 50, offset: int = 0) -> tuple[Sequence[AgentRun], int]:
        with self._session_factory() as session:
            runs = AgentRunRepository(session)
            return runs.search(limit=limit, offset=offset), runs.count()

    def _finish(self, run_id: int, state: AgentState, *, error: str | None = None) -> AgentRun:
        with self._session_factory() as session:
            run = AgentRunRepository(session).get(run_id)
            assert run is not None  # it was read when the run began
            run.tool_calls = state["tool_calls"]
            run.submissions = state["submissions"]
            run.model_calls = state["model_calls"]
            run.input_tokens = state["input_tokens"]
            run.output_tokens = state["output_tokens"]
            run.summary = _summary(state)
            run.finished_at = self._clock()
            if error is None:
                run.status = AgentRunStatus.COMPLETED
                run.outcome = _outcome(state)
            else:
                run.status = AgentRunStatus.FAILED
                run.error = error[:MAX_ERROR_LENGTH]
            session.commit()
            return run


def _outcome(state: AgentState) -> AgentRunOutcome:
    if state["accepted"]:
        return AgentRunOutcome.PROPOSED
    if state["stop"] == "retries_exhausted":
        return AgentRunOutcome.REJECTED
    if state["stop"] == "step_limit":
        return AgentRunOutcome.STEP_LIMIT
    return AgentRunOutcome.NO_PROPOSAL


def _summary(state: AgentState) -> str | None:
    if state["stop"] != "answered":
        return None
    content: Any = state["messages"][-1].content
    # Some providers answer with a list of content blocks instead of a string.
    if isinstance(content, list):
        content = "".join(
            block if isinstance(block, str) else str(block.get("text", "")) for block in content
        )
    return str(content) or None
