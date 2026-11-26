"""Runs a stopped process left "running": found with the database clock, marked
failed, and with what they did up to their last saved step."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.models import AgentRun
from stockpilot.repositories.agent_runs import AgentRunRepository
from stockpilot.services.agent_runs import AgentRunService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=120)


def _service(
    session_factory: Callable[[], Session], saver: InMemorySaver | None = None
) -> AgentRunService:
    checkpointer = saver or InMemorySaver()
    return AgentRunService(
        session_factory,
        [],
        checkpointer=lambda: checkpointer,
        clock=lambda: NOW,
        max_model_calls=10,
    )


def _run(session: Session, status: AgentRunStatus, *, minutes_ago: int, **fields: Any) -> AgentRun:
    run = AgentRun(
        status=status,
        trigger=AgentRunTrigger.SCHEDULE,
        request={},
        llm_provider="fake",
        llm_model="fake",
        tool_calls=[],
        submissions=[],
        **fields,
    )
    session.add(run)
    session.flush()
    # The database clock: the one the query compares with. Inside the test's
    # transaction it does not move, so the minutes are exact.
    run.updated_at = func.now() - timedelta(minutes=minutes_ago)
    session.flush()
    return run


def test_a_run_running_too_long_is_marked_failed(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    run = _run(db_session, AgentRunStatus.RUNNING, minutes_ago=300)

    assert _service(session_factory).fail_abandoned(LIMIT) == [run.id]

    db_session.refresh(run)
    assert run.status is AgentRunStatus.FAILED
    assert run.finished_at == NOW
    assert run.error is not None
    assert run.error.startswith("Still running 120 minutes after it started or resumed")


@pytest.mark.parametrize(("minutes_ago", "abandoned"), [(119, False), (120, False), (121, True)])
def test_only_after_the_limit(
    db_session: Session,
    session_factory: Callable[[], Session],
    minutes_ago: int,
    abandoned: bool,
) -> None:
    run = _run(db_session, AgentRunStatus.RUNNING, minutes_ago=minutes_ago)

    assert _service(session_factory).fail_abandoned(LIMIT) == ([run.id] if abandoned else [])


def test_runs_waiting_for_people_or_finished_are_left_alone(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    # Waiting for days is normal: people decide when they can.
    waiting = _run(db_session, AgentRunStatus.WAITING, minutes_ago=5 * 24 * 60)
    completed = _run(
        db_session,
        AgentRunStatus.COMPLETED,
        minutes_ago=300,
        outcome=AgentRunOutcome.NO_PROPOSAL,
        finished_at=NOW,
    )

    assert _service(session_factory).fail_abandoned(LIMIT) == []

    db_session.refresh(waiting)
    db_session.refresh(completed)
    assert waiting.status is AgentRunStatus.WAITING
    assert completed.status is AgentRunStatus.COMPLETED


def test_a_run_resumed_after_days_counts_from_when_it_resumed(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    run = _run(db_session, AgentRunStatus.WAITING, minutes_ago=3 * 24 * 60)

    # What resume_if_decided does: the status change refreshes updated_at.
    run.status = AgentRunStatus.RUNNING
    db_session.flush()

    assert run.updated_at == db_session.scalar(select(func.now()))
    assert _service(session_factory).fail_abandoned(LIMIT) == []


def test_keeps_what_it_did_up_to_its_last_saved_step(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    run = _run(db_session, AgentRunStatus.RUNNING, minutes_ago=300)
    saver = InMemorySaver()
    values: dict[str, Any] = {
        "tool_calls": [{"name": "get_dealers_with_shortage", "args": {}}],
        "submissions": [{"vin": "8AFZZZ00000000001", "order_id": 7}],
        "model_calls": 2,
        "input_tokens": 3000,
        "output_tokens": 120,
    }
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = values
    checkpoint["channel_versions"] = dict.fromkeys(values, 1)
    saver.put(
        {"configurable": {"thread_id": f"agent-run-{run.id}", "checkpoint_ns": ""}},
        checkpoint,
        {},
        checkpoint["channel_versions"],
    )

    _service(session_factory, saver).fail_abandoned(LIMIT)

    db_session.refresh(run)
    assert run.status is AgentRunStatus.FAILED
    assert run.tool_calls == values["tool_calls"]
    assert run.submissions == values["submissions"]
    assert (run.model_calls, run.input_tokens, run.output_tokens) == (2, 3000, 120)


def test_checked_again_only_among_the_runs_found_first(
    db_session: Session, session_factory: Callable[[], Session]
) -> None:
    first = _run(db_session, AgentRunStatus.RUNNING, minutes_ago=300)
    _run(db_session, AgentRunStatus.RUNNING, minutes_ago=300)

    with session_factory() as session:
        found = AgentRunRepository(session).abandoned(LIMIT, among=[first.id])

    assert [run.id for run in found] == [first.id]


def test_any_running(db_session: Session, session_factory: Callable[[], Session]) -> None:
    service = _service(session_factory)
    _run(db_session, AgentRunStatus.WAITING, minutes_ago=0)
    assert service.any_running() is False

    _run(db_session, AgentRunStatus.RUNNING, minutes_ago=0)
    assert service.any_running() is True


# --- A run being resumed right now --------------------------------------------


@pytest.fixture
def committed_run(migrated_engine: Engine) -> Iterator[int]:
    """Committed, so another connection can lock it."""
    with Session(migrated_engine) as session:
        run = _run(session, AgentRunStatus.RUNNING, minutes_ago=300)
        session.commit()
        run_id = run.id
    try:
        yield run_id
    finally:
        with Session(migrated_engine) as session:
            session.execute(delete(AgentRun).where(AgentRun.id == run_id))
            session.commit()


def test_a_run_locked_by_someone_else_is_skipped(
    postgres_url: str, migrated_engine: Engine, committed_run: int
) -> None:
    # If the query waited for the lock instead, this fails in 2 s instead of hanging.
    engine = create_engine(postgres_url, connect_args={"options": "-c lock_timeout=2000"})
    try:
        with migrated_engine.connect() as other:
            # Like resume_if_decided, holding the row while it changes the status.
            other.execute(
                text("SELECT id FROM agent_runs WHERE id = :id FOR UPDATE"), {"id": committed_run}
            )

            assert _service(sessionmaker(engine)).fail_abandoned(LIMIT) == []
    finally:
        engine.dispose()
