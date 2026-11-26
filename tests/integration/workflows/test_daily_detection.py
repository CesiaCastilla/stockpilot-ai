"""The daily job: what it measures, when it calls the agent and when it does not.

The networks are built like the eval scenarios, from the same small description.
Only the model is fake.
"""

import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, create_engine, delete, func, make_url, select, text
from sqlalchemy.orm import Session

from stockpilot.agents.checkpoints import CHECKPOINT_TABLES
from stockpilot.agents.graph import SUBMIT
from stockpilot.core.config import Settings
from stockpilot.core.logging import configure_logging
from stockpilot.domain.agent_runs import AgentRunOutcome, AgentRunStatus, AgentRunTrigger
from stockpilot.domain.transfers import TransferOrderStatus
from stockpilot.models import AgentRun, Dealer, Sale, TransferOrder, Trim, Unit, VehicleModel
from stockpilot.schemas.agent_runs import AgentRunRequest
from stockpilot.services.agent_runs import AgentRunService
from stockpilot.workflows.daily_detection import (
    ANOTHER_RUN,
    NOTHING_TO_FIX,
    DailyDetection,
    DetectionReport,
    run,
)
from tests.evals.scenario import Scenario
from tests.evals.world import NOW, World, build_world
from tests.fakes import ScriptedChatModel, calls, says, scripted
from tests.integration.conftest import PROJECT_ROOT

pytestmark = pytest.mark.integration

CORDOBA = {"city": "Córdoba", "at": [-31.42, -64.18]}
MENDOZA = {"city": "Mendoza", "at": [-32.89, -68.84]}

# D001 sold out of a trim that D002 has had for 130 days. D002 also has a
# young unit of another trim: not aged, not short.
SOLD_OUT: dict[str, Any] = {
    "dealers": {"D001": CORDOBA, "D002": MENDOZA},
    "stock": [
        {"dealer": "D002", "trim": "Aconcagua SR", "days": 130, "label": "aged"},
        {"dealer": "D002", "trim": "Tandil Start", "days": 20},
    ],
    "sales": [{"dealer": "D001", "trim": "Aconcagua SR", "count": 6}],
}
# The same shortage, but D001's yard is full: no transfer can fix it.
FULL_YARD: dict[str, Any] = {
    **SOLD_OUT,
    "dealers": {"D001": {**CORDOBA, "capacity": 2}, "D002": MENDOZA},
    "stock": [
        *SOLD_OUT["stock"],
        {"dealer": "D001", "trim": "Tandil Start", "days": 20, "count": 2},
    ],
}
# Stock and no sales: nothing is short.
QUIET: dict[str, Any] = {
    "dealers": {"D001": CORDOBA},
    "stock": [{"dealer": "D001", "trim": "Aconcagua SR", "days": 30, "count": 3}],
}


def _world(session: Session, network: dict[str, Any]) -> World:
    return build_world(
        session, Scenario.model_validate({"id": "job", "title": "job", "expect": {}, **network})
    )


def _proposal(world: World) -> dict[str, Any]:
    vin = next(vin for vin, label in world.labels.items() if label == "aged")
    return {
        "vin": vin,
        "destination_dealer_id": world.dealers["D001"].id,
        "reason": "D001 vendió 6 en 60 días y no tiene stock; esta lleva 130 días en stock.",
    }


@pytest.fixture
def detection(session_factory: Callable[[], Session]) -> DailyDetection:
    checkpointer = InMemorySaver()
    runs = AgentRunService(
        session_factory,
        [],
        checkpointer=lambda: checkpointer,
        clock=lambda: NOW,
        max_model_calls=10,
    )
    return DailyDetection(
        session_factory,
        runs,
        Settings(_env_file=None).coverage_policy(),
        lambda: NOW,
        abandoned_after=timedelta(minutes=120),
    )


def _detect(detection: DailyDetection, model: Callable[[], BaseChatModel]) -> DetectionReport:
    return detection(model, provider="fake", model_name="scripted")


def _runs(session: Session) -> list[AgentRun]:
    return list(session.scalars(select(AgentRun).order_by(AgentRun.id)))


def _running_run(session: Session, *, minutes_ago: int) -> AgentRun:
    run = AgentRun(
        status=AgentRunStatus.RUNNING,
        trigger=AgentRunTrigger.API,
        request={},
        llm_provider="fake",
        llm_model="fake",
        tool_calls=[],
        submissions=[],
    )
    session.add(run)
    session.flush()
    run.updated_at = func.now() - timedelta(minutes=minutes_ago)
    session.flush()
    return run


def test_lets_the_agent_propose_what_a_transfer_can_fix(
    db_session: Session, detection: DailyDetection
) -> None:
    world = _world(db_session, SOLD_OUT)
    model = scripted(calls((SUBMIT, {"proposals": [_proposal(world)]})))

    report = _detect(detection, lambda: model)

    assert (report.shortages, report.can_be_served, report.aged_units) == (1, 1, 1)
    assert report.skipped is None
    assert report.run is not None
    assert report.run.trigger is AgentRunTrigger.SCHEDULE
    assert report.run.request == AgentRunRequest().model_dump(mode="json")
    # Its order waits for a person, and the run waits with it.
    assert report.run.status is AgentRunStatus.WAITING
    order = db_session.scalars(select(TransferOrder)).one()
    assert order.agent_run_id == report.run.id
    assert order.status is TransferOrderStatus.PENDING


@pytest.mark.parametrize(("network", "shortages"), [(QUIET, 0), (FULL_YARD, 1)])
def test_does_not_call_the_model_when_no_transfer_can_fix_anything(
    db_session: Session, detection: DailyDetection, network: dict[str, Any], shortages: int
) -> None:
    _world(db_session, network)
    model = scripted()

    report = _detect(detection, lambda: model)

    assert (report.shortages, report.can_be_served) == (shortages, 0)
    assert report.skipped == NOTHING_TO_FIX
    assert report.run is None
    assert model.received == []
    assert _runs(db_session) == []


def test_waits_for_a_run_in_progress(db_session: Session, detection: DailyDetection) -> None:
    _world(db_session, SOLD_OUT)
    in_progress = _running_run(db_session, minutes_ago=10)
    model = scripted()

    report = _detect(detection, lambda: model)

    assert report.skipped == ANOTHER_RUN
    assert model.received == []
    assert _runs(db_session) == [in_progress]


def test_a_run_left_behind_does_not_block_the_morning(
    db_session: Session, detection: DailyDetection
) -> None:
    _world(db_session, SOLD_OUT)
    left_behind = _running_run(db_session, minutes_ago=300)

    report = _detect(detection, lambda: scripted(says("Nada para mover.")))

    assert report.abandoned_runs == [left_behind.id]
    db_session.refresh(left_behind)
    assert left_behind.status is AgentRunStatus.FAILED
    assert report.run is not None
    assert report.run.status is AgentRunStatus.COMPLETED


def test_a_model_that_fails_leaves_a_failed_run(
    db_session: Session, detection: DailyDetection
) -> None:
    _world(db_session, SOLD_OUT)

    def quota_exhausted() -> BaseChatModel:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    report = _detect(detection, quota_exhausted)

    assert report.run is not None
    assert report.run.status is AgentRunStatus.FAILED
    assert report.run.error == "RuntimeError: 429 RESOURCE_EXHAUSTED"


# --- The job as the scheduler runs it -----------------------------------------
# Its own engine, HTTP client and checkpointer, all from the settings: the data
# is committed, so it is cleaned up by hand.


@pytest.fixture
def job_settings(postgres_url: str, migrated_engine: Engine) -> Iterator[Settings]:
    try:
        with Session(migrated_engine) as session:
            _world(session, SOLD_OUT)
            session.commit()
        yield Settings(_env_file=None, database_url=postgres_url, log_format="json")
    finally:
        configure_logging(Settings(_env_file=None))
        with Session(migrated_engine) as session:
            states = sorted((CHECKPOINT_TABLES - {"checkpoint_migrations"}) & _tables(session))
            for table in states:
                session.execute(text(f"DELETE FROM {table} WHERE thread_id LIKE 'agent-run-%'"))
            for model in (TransferOrder, AgentRun, Sale, Unit, Trim, VehicleModel, Dealer):
                session.execute(delete(model))
            session.commit()


def _tables(session: Session) -> set[str]:
    rows = session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    return {row[0] for row in rows}


@pytest.mark.parametrize(
    ("answers", "exit_code", "status"),
    [
        ([says("Nada para mover hoy.")], 0, AgentRunStatus.COMPLETED),
        # The script runs out on the first call: the model failed.
        ([], 1, AgentRunStatus.FAILED),
    ],
)
def test_the_job_runs_the_agent_and_tells_how_it_went(
    job_settings: Settings,
    migrated_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    answers: list[Any],
    exit_code: int,
    status: AgentRunStatus,
) -> None:
    model: ScriptedChatModel = scripted(*answers)

    assert run(job_settings, chat_model=lambda _: model) == exit_code

    with Session(migrated_engine) as session:
        agent_run = session.scalars(select(AgentRun)).one()
    assert agent_run.trigger is AgentRunTrigger.SCHEDULE
    assert agent_run.status is status
    if status is AgentRunStatus.COMPLETED:
        assert agent_run.outcome is AgentRunOutcome.NO_PROPOSAL

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    done = next(e for e in events if e["message"] == "daily_detection_done")
    assert done | {"correlation_id": None, "time": None} == {
        "message": "daily_detection_done",
        "severity": "INFO",
        "logger": "stockpilot.workflows.daily_detection",
        "shortages": 1,
        "can_be_served": 1,
        "aged_units": 1,
        "abandoned_runs": [],
        "skipped": None,
        "agent_run_id": agent_run.id,
        "status": status.value,
        "outcome": "no_proposal" if status is AgentRunStatus.COMPLETED else None,
        "orders": 0,
        "correlation_id": None,
        "time": None,
    }
    # Every event of the job, the agent's included, carries the same id.
    assert len({e["correlation_id"] for e in events}) == 1


@pytest.fixture
def fresh_database(postgres_url: str, migrated_engine: Engine) -> Iterator[str]:
    """A new database, migrated, where the checkpointer was never used."""
    name = f"fresh_{uuid4().hex[:12]}"
    admin = migrated_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(postgres_url).set(database=name).render_as_string(hide_password=False)
    try:
        config = Config(
            file_=PROJECT_ROOT / "alembic.ini", toml_file=PROJECT_ROOT / "pyproject.toml"
        )
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url
    finally:
        configure_logging(Settings(_env_file=None))
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


def test_a_run_left_behind_on_the_first_morning_of_the_checkpointer(
    fresh_database: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Found live: reading the run's saved state while holding it locked made the
    # checkpointer's first CREATE INDEX CONCURRENTLY wait for the job, forever.
    engine = create_engine(fresh_database)
    try:
        with Session(engine) as session:
            _world(session, SOLD_OUT)
            left_behind = _running_run(session, minutes_ago=300).id
            session.commit()
        # Every connection gives up waiting for a lock after 5 s, instead of hanging.
        monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=5000")
        settings = Settings(_env_file=None, database_url=fresh_database, log_format="json")

        assert run(settings, chat_model=lambda _: scripted(says("Nada para mover."))) == 0

        with Session(engine) as session:
            statuses = {r.id: r.status for r in session.scalars(select(AgentRun))}
    finally:
        engine.dispose()
    assert statuses.pop(left_behind) is AgentRunStatus.FAILED
    assert list(statuses.values()) == [AgentRunStatus.COMPLETED]
    events = [json.loads(line)["message"] for line in capsys.readouterr().out.splitlines()]
    assert "agent_run_state_unavailable" not in events


def test_without_an_ai_model_it_fails_before_touching_the_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A database that does not exist: reaching it would be another error.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://nobody:x@127.0.0.1:1/none",
        google_api_key=None,
        log_format="json",
    )
    try:
        assert run(settings) == 1
    finally:
        configure_logging(Settings(_env_file=None))

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["message"] for e in events] == ["daily_detection_failed"]
