import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect

from stockpilot.models import Base

pytestmark = pytest.mark.integration

APP_TABLES = {"vehicle_models", "trims", "dealers", "units", "reservations", "sales", "unit_events"}


def test_models_match_the_latest_migration(migrated_engine: Engine) -> None:
    # Fails if someone changes a model and forgets to generate its migration.
    with migrated_engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diff == []


def test_migrations_downgrade_to_empty_and_upgrade_back(
    migrated_engine: Engine, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "base")
    assert APP_TABLES.isdisjoint(inspect(migrated_engine).get_table_names())

    # Leave the schema at head for the rest of the session.
    command.upgrade(alembic_config, "head")
    assert APP_TABLES.issubset(inspect(migrated_engine).get_table_names())
