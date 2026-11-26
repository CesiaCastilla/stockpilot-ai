"""add waiting agent runs and their stop reason

Revision ID: 3c1d2e7f9a10
Revises: 959550fe7b3f
Create Date: 2026-11-26 16:00:00.000000

Written by hand: autogenerate does not detect changes to CHECK constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c1d2e7f9a10"
down_revision: str | Sequence[str] | None = "959550fe7b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_runs"


def _replace(name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, type_="check")
    op.create_check_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, condition)


def upgrade() -> None:
    """Upgrade schema."""
    _replace("status", "status IN ('running', 'waiting', 'completed', 'failed')")
    _replace("finished_has_date", "(status IN ('running', 'waiting')) = (finished_at IS NULL)")
    op.add_column(TABLE, sa.Column("stop_reason", sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column(TABLE, "stop_reason")
    # A waiting run cannot be represented before this revision: it ends as failed.
    op.execute(
        f"UPDATE {TABLE} SET status = 'failed', finished_at = now(), "
        "error = 'Stopped waiting: the database was downgraded' WHERE status = 'waiting'"
    )
    _replace("finished_has_date", "(status = 'running') = (finished_at IS NULL)")
    _replace("status", "status IN ('running', 'completed', 'failed')")
