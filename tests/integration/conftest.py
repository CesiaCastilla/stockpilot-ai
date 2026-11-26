"""Shared fixtures for integration tests: one PostgreSQL container per test session."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from stockpilot.api.dependencies import get_agent_run_service, get_chat_model
from stockpilot.core.config import Settings
from stockpilot.core.database import get_session
from stockpilot.main import create_app
from stockpilot.services.agent_runs import AgentRunService
from tests.fakes import says, scripted

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    # Same major version as docker-compose.yml and Cloud SQL.
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def alembic_config(postgres_url: str) -> Config:
    config = Config(
        file_=PROJECT_ROOT / "alembic.ini",
        toml_file=PROJECT_ROOT / "pyproject.toml",
    )
    config.set_main_option("sqlalchemy.url", postgres_url)
    return config


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str, alembic_config: Config) -> Iterator[Engine]:
    """Engine for a database built only from the Alembic migrations, never create_all()."""
    command.upgrade(alembic_config, "head")
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """Session whose changes are rolled back after each test, so tests never see each other's data.

    Commits inside the test become savepoints of an outer transaction that is
    always rolled back.
    """
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def session_factory(db_session: Session) -> Callable[[], Session]:
    """New sessions inside the test's transaction, for code that opens its own
    (the agent and its tools). They see the test's data and are rolled back with it."""
    connection = db_session.connection()

    def new_session() -> Session:
        return Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    return new_session


@pytest.fixture
def api_client(
    postgres_url: str, db_session: Session, session_factory: Callable[[], Session]
) -> Iterator[TestClient]:
    """HTTP client whose requests share db_session, so their writes are rolled back too.

    Tests can also arrange data through db_session and the API will see it. An
    agent run started here uses a fake model that finds nothing to move.
    """
    app = create_app(Settings(database_url=postgres_url))
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_chat_model] = lambda: scripted(says("Nothing to move."))
    checkpointer = InMemorySaver()
    app.dependency_overrides[get_agent_run_service] = lambda: AgentRunService(
        session_factory,
        [],
        checkpointer=lambda: checkpointer,
        clock=lambda: datetime.now(UTC),
        max_model_calls=10,
    )
    with TestClient(app) as client:
        yield client
