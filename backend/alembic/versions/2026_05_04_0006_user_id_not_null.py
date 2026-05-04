"""tighten user_id columns to NOT NULL on per-user tables

Revision ID: 0006_user_id_not_null
Revises: 0005_user_id_columns
Create Date: 2026-05-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006_user_id_not_null"
down_revision: str | None = "0005_user_id_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PER_USER_TABLES: tuple[str, ...] = (
    "predictions",
    "paper_trades",
    "shadow_trades",
    "shadow_open_positions",
    "shadow_cooldowns",
)


def upgrade() -> None:
    for table in PER_USER_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL;")


def downgrade() -> None:
    for table in PER_USER_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN user_id DROP NOT NULL;")
