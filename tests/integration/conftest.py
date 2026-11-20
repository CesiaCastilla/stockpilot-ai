"""Shared fixtures for integration tests: one PostgreSQL container per test session."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

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
