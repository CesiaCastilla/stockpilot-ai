from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy.orm import Session

from stockpilot.agents.graph import AgentLimits, AgentState, build_graph, initial_state
from stockpilot.agents.prompts import system_prompt, task
from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.domain.errors import NotFoundError
from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models import AgentRun
from stockpilot.repositories.agent_runs import AgentRunRepository
from stockpilot.repositories.transfer_orders import TransferOrderRepository
from stockpilot.schemas.agent_runs import AgentRunRequest
from stockpilot.schemas.transfers import TransferProposal
from stockpilot.services.transfers import TransferService

logger = structlog.get_logger(__name__)

# The error is stored for people to read, not to keep a whole traceback.
MAX_ERROR_LENGTH = 2000

ABANDONED = (
    "Still running {minutes} minutes after it started or resumed: the process running it "
    "stopped. What it did up to its last saved step is kept; its pending orders stay pending."
)


# Called only when the graph needs the model, so a run whose orders were all
# approved can finish even without an AI model configured.
ModelFactory = Callable[[], BaseChatModel]


class AgentRunService:
    """Starts agent runs, resumes them after people decide, and records what each did.

    A run lasts as long as the model takes, and may pause for hours, so it does
    not hold one session: each write opens its own, and so does each tool call.
    Its graph state lives in the checkpointer between steps.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        tools: Sequence[BaseTool],
        *,
        checkpointer: Callable[[], BaseCheckpointSaver[str]],
        clock: Callable[[], datetime],
        max_model_calls: int,
    ) -> None:
        self._session_factory = session_factory
        self._tools = tools
        self._checkpointer = checkpointer
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

    def execute(self, run_id: int, model: ModelFactory) -> AgentRun:
        """Run the agent until it ends or pauses for people to decide. Never raises:
        a failure is a failed run, so a background task always leaves a record."""
        run = self.get(run_id)
        request = AgentRunRequest.model_validate(run.request)
        logger.info("agent_run_started", agent_run_id=run_id, request=run.request)
        return self._advance(run_id, request, model, initial_state(task(request)))

    def resume_if_decided(self, run_id: int, model: ModelFactory) -> AgentRun | None:
        """Resume a waiting run once none of its orders is pending. Called after
        every decision; only the last one resumes it, and only once."""
        with self._session_factory() as session:
            # Locked: two decisions finishing at once cannot both resume the run.
            run = AgentRunRepository(session).get_for_update(run_id)
            if run is None or run.status is not AgentRunStatus.WAITING:
                return None
            orders = TransferOrderRepository(session).for_run(run_id)
            if any(o.status is TransferOrderStatus.PENDING for o in orders):
                return None
            decisions = [
                {
                    "order_id": o.id,
                    "vin": o.unit.vin,
                    "destination_dealer_id": o.destination_dealer_id,
                    "status": o.status.value,
                    "rejection_reason": o.rejection_reason,
                }
                for o in orders
            ]
            run.status = AgentRunStatus.RUNNING
            session.commit()
            request = AgentRunRequest.model_validate(run.request)
        logger.info("agent_run_resumed", agent_run_id=run_id)
        return self._advance(run_id, request, model, Command(resume=decisions))

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

    def any_running(self) -> bool:
        with self._session_factory() as session:
            return AgentRunRepository(session).any_running()

    def fail_abandoned(self, older_than: timedelta) -> list[int]:
        """Mark as failed the runs a stopped process left running, with what they
        did up to their last saved step. Otherwise they would look alive forever.

        Not resumed from the checkpoint: a process that died right after creating
        an order, before saving the step, would propose it again on replay.
        """
        with self._session_factory() as session:
            found = [run.id for run in AgentRunRepository(session).abandoned(older_than)]
        if not found:
            return []
        # Read with no transaction open: on its first use the checkpointer creates
        # its indexes CONCURRENTLY, which waits for every open transaction to end.
        # Holding the rows locked meanwhile, the job would wait for itself forever.
        states = {run_id: self._saved_state(_thread(run_id)) for run_id in found}
        with self._session_factory() as session:
            # Locked and checked again: one may have ended or resumed in between.
            runs = AgentRunRepository(session).abandoned(older_than, among=found)
            for run in runs:
                _copy_state(run, states[run.id])
                run.status = AgentRunStatus.FAILED
                run.error = ABANDONED.format(minutes=int(older_than.total_seconds() // 60))
                run.finished_at = self._clock()
            session.commit()
            failed = [run.id for run in runs]
        for run_id in failed:
            logger.warning("agent_run_abandoned", agent_run_id=run_id)
        return failed

    def _advance(
        self,
        run_id: int,
        request: AgentRunRequest,
        model: ModelFactory,
        graph_input: AgentState | Command[Any],
    ) -> AgentRun:
        def propose(proposal: TransferProposal) -> int:
            with self._session_factory() as session:
                return TransferService(session).propose(proposal, agent_run_id=run_id).id

        log = logger.bind(agent_run_id=run_id)
        config = _thread(run_id)
        config["recursion_limit"] = 4 * self._max_model_calls
        try:
            graph = build_graph(
                model,
                self._tools,
                propose,
                system_prompt=system_prompt(self._clock().date()),
                limits=AgentLimits(
                    max_proposals=request.max_proposals, max_model_calls=self._max_model_calls
                ),
                checkpointer=self._checkpointer(),
            )
            # Step by step, so a failure halfway still records what happened before it.
            for _ in graph.stream(graph_input, config, stream_mode="updates"):
                pass
        except Exception as error:  # the model, the network, a bug: all end the same way
            log.exception("agent_run_failed")
            return self._record(
                run_id, self._saved_state(config), error=f"{type(error).__name__}: {error}"
            )
        snapshot = graph.get_state(config)
        state = cast(AgentState, snapshot.values)
        waiting = bool(snapshot.next)
        log.info("agent_run_paused" if waiting else "agent_run_finished", stop=state["stop"])
        return self._record(run_id, state, waiting=waiting)

    def _saved_state(self, config: RunnableConfig) -> AgentState | None:
        """What the checkpointer kept after the last step that finished, if anything."""
        try:
            values = self._checkpointer().get_tuple(config)
        except Exception:  # the checkpointer itself may be what failed
            logger.exception("agent_run_state_unavailable")
            return None
        if values is None:
            return None
        return cast(AgentState, values.checkpoint["channel_values"])

    def _record(
        self,
        run_id: int,
        state: AgentState | None,
        *,
        waiting: bool = False,
        error: str | None = None,
    ) -> AgentRun:
        with self._session_factory() as session:
            run = AgentRunRepository(session).get(run_id)
            assert run is not None  # it was read when the run began
            _copy_state(run, state)
            if error is not None:
                run.status = AgentRunStatus.FAILED
                run.error = error[:MAX_ERROR_LENGTH]
                run.finished_at = self._clock()
            elif waiting:
                run.status = AgentRunStatus.WAITING
            else:
                run.status = AgentRunStatus.COMPLETED
                run.outcome = _outcome(state) if state is not None else AgentRunOutcome.NO_PROPOSAL
                run.finished_at = self._clock()
            session.commit()
            return run


def _thread(run_id: int) -> RunnableConfig:
    # One thread per run: the checkpointer keeps its state under this id.
    return {"configurable": {"thread_id": f"agent-run-{run_id}"}}


def _copy_state(run: AgentRun, state: AgentState | None) -> None:
    """What the graph did, into the audit row."""
    if state is None:
        return
    run.tool_calls = state.get("tool_calls", [])
    run.submissions = state.get("submissions", [])
    run.model_calls = state.get("model_calls", 0)
    run.input_tokens = state.get("input_tokens", 0)
    run.output_tokens = state.get("output_tokens", 0)
    run.summary = _summary(state)
    run.stop_reason = state.get("stop")


def _outcome(state: AgentState) -> AgentRunOutcome:
    if state["accepted"]:
        return AgentRunOutcome.PROPOSED
    if state["stop"] == "retries_exhausted":
        return AgentRunOutcome.REJECTED
    if state["stop"] == "step_limit":
        return AgentRunOutcome.STEP_LIMIT
    return AgentRunOutcome.NO_PROPOSAL


def _summary(state: AgentState) -> str | None:
    if state.get("stop") != "answered":
        return None
    content: Any = state["messages"][-1].content
    # Some providers answer with a list of content blocks instead of a string.
    if isinstance(content, list):
        content = "".join(
            block if isinstance(block, str) else str(block.get("text", "")) for block in content
        )
    return str(content) or None
