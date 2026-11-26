"""LangGraph's checkpointer against a real PostgreSQL."""

import pytest
from sqlalchemy import Engine, inspect, text

from stockpilot.agents.checkpoints import APPLICATION_NAME, CHECKPOINT_TABLES, CheckpointStore

pytestmark = pytest.mark.integration


def test_tables_are_created_on_first_use_and_the_saver_is_reused(
    postgres_url: str, migrated_engine: Engine
) -> None:
    store = CheckpointStore(postgres_url)
    try:
        first = store.saver()

        assert store.saver() is first
        assert set(inspect(migrated_engine).get_table_names()) >= CHECKPOINT_TABLES
    finally:
        store.close()


def test_a_closed_store_opens_again(postgres_url: str) -> None:
    store = CheckpointStore(postgres_url)
    first = store.saver()
    store.close()

    try:
        assert store.saver() is not first
    finally:
        store.close()
        store.close()  # closing twice is harmless


def test_creating_the_store_does_not_connect() -> None:
    # The API starts, and /health answers, while the database is down.
    store = CheckpointStore("postgresql+psycopg://nobody:x@localhost:1/none")

    store.close()


def test_closing_releases_the_connections(postgres_url: str, migrated_engine: Engine) -> None:
    def open_connections() -> int:
        query = text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :name")
        with migrated_engine.connect() as connection:
            return int(connection.scalar(query, {"name": APPLICATION_NAME}) or 0)

    store = CheckpointStore(postgres_url)
    saver = store.saver()  # still referenced, like by a run in progress
    assert open_connections() >= 1

    store.close()

    assert open_connections() == 0
    assert saver is not None
