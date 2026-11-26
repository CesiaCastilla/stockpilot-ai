"""Where paused agent runs are kept: LangGraph's PostgreSQL checkpointer.

A run that waits for people to decide is saved here after every step, so it
can resume hours later, after a restart, in another process.
"""

import threading
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import make_url

# Created by LangGraph, not by our migrations: Alembic must leave them alone.
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


APPLICATION_NAME = "stockpilot-checkpoints"


def skip_checkpoint_tables(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Alembic's include_object hook: autogenerate would otherwise propose
    dropping LangGraph's tables, which are not in our models."""
    table = obj.table.name if type_ in {"index", "column", "foreign_key_constraint"} else name
    return not (reflected and table in CHECKPOINT_TABLES)


class CheckpointStore:
    """Opens the pool and creates the tables on first use, not at startup: the
    API must start, and answer /health, even while the database is down.

    The tables are not in an Alembic migration because LangGraph creates some
    indexes CONCURRENTLY, which cannot run inside a migration's transaction.
    """

    def __init__(self, database_url: str) -> None:
        # psycopg wants a plain libpq URL, without SQLAlchemy's "+psycopg".
        self._url = make_url(database_url).set(drivername="postgresql")
        self._lock = threading.Lock()
        self._pool: ConnectionPool[Connection[DictRow]] | None = None
        self._saver: PostgresSaver | None = None

    def saver(self) -> BaseCheckpointSaver[str]:
        with self._lock:
            if self._saver is None:
                self._pool = ConnectionPool(
                    self._url.render_as_string(hide_password=False),
                    connection_class=Connection[DictRow],
                    min_size=1,
                    max_size=5,
                    # What PostgresSaver requires of its connections.
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                        # Tells these connections apart in pg_stat_activity.
                        "application_name": APPLICATION_NAME,
                    },
                    open=True,
                )
                saver = PostgresSaver(self._pool)
                saver.setup()  # idempotent: it records its own migrations
                self._saver = saver
            return self._saver

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close()
            self._pool, self._saver = None, None
