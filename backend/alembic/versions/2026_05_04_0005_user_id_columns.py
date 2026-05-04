"""user_id columns on per-user tables + bootstrap admin seed + backfill

Revision ID: 0005_user_id_columns
Revises: 0004_users_and_invitations
Create Date: 2026-05-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_user_id_columns"
down_revision: str | None = "0004_users_and_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that gain a nullable user_id column in this migration.
# Order matters only insofar as we backfill them all in the same transaction.
PER_USER_TABLES: tuple[str, ...] = (
    "predictions",
    "paper_trades",
    "shadow_trades",
    "shadow_open_positions",
    "shadow_cooldowns",
)

BOOTSTRAP_ADMIN_EMAIL: str = "nagarajan1998.yuva@gmail.com"


def upgrade() -> None:
    # Step 1: Seed bootstrap admin row (id=1). ON CONFLICT DO NOTHING so re-running is safe.
    op.execute(
        sa.text(
            """
            INSERT INTO users (id, email, display_name, is_admin, is_active)
            VALUES (1, :email, 'Admin', TRUE, TRUE)
            ON CONFLICT (email) DO NOTHING
            """
        ).bindparams(email=BOOTSTRAP_ADMIN_EMAIL)
    )
    # Reset the BIGSERIAL sequence so the next user gets id=2, not id=1 colliding.
    op.execute(
        "SELECT setval(pg_get_serial_sequence('users', 'id'), "
        "GREATEST((SELECT MAX(id) FROM users), 1));"
    )

    # Step 2: Add nullable user_id column to each per-user table.
    for table in PER_USER_TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN user_id BIGINT REFERENCES users(id);"
        )

    # Step 3: Backfill: every existing row belongs to user_id=1 (bootstrap admin).
    for table in PER_USER_TABLES:
        op.execute(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL;")

    # Step 4: Per-table indexes on user_id (so per-user queries stay fast).
    op.execute(
        "CREATE INDEX predictions_user_id_idx ON predictions (user_id, ts DESC);"
    )
    op.execute(
        "CREATE INDEX paper_trades_user_id_idx ON paper_trades (user_id, opened_at DESC);"
    )
    op.execute(
        "CREATE INDEX shadow_trades_user_id_idx ON shadow_trades (user_id, opened_at DESC);"
    )
    op.execute(
        "CREATE INDEX shadow_open_positions_user_id_idx ON shadow_open_positions (user_id);"
    )
    op.execute(
        "CREATE INDEX shadow_cooldowns_user_id_idx ON shadow_cooldowns (user_id);"
    )


def downgrade() -> None:
    for table in PER_USER_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {table}_user_id_idx;")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS user_id;")
    op.execute(
        sa.text("DELETE FROM users WHERE email = :email").bindparams(
            email=BOOTSTRAP_ADMIN_EMAIL
        )
    )
