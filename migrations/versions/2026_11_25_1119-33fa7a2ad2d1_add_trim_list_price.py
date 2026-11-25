"""add trim list price

Revision ID: 33fa7a2ad2d1
Revises: 5abbbeb6eb24
Create Date: 2026-11-25 11:19:43.730877

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "33fa7a2ad2d1"
down_revision: str | Sequence[str] | None = "5abbbeb6eb24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable: the trims that already exist have no price yet.
    op.add_column(
        "trims", sa.Column("list_price_usd", sa.Numeric(precision=10, scale=2), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_trims_list_price_usd_positive"), "trims", "list_price_usd > 0"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("ck_trims_list_price_usd_positive"), "trims", type_="check")
    op.drop_column("trims", "list_price_usd")
