"""PR8: live_cooldowns table — outcome-adaptive cooldown for live trades.

Per the PR8 design spec (docs/superpowers/specs/2026-05-18-pr8-outcome-
adaptive-cooldown-design.md §6.1), this migration creates the
`live_cooldowns` state table that the dispatcher consults during the
pre-conditions block and that `live_exit_monitor` upserts on trade close.

Schema rationale:
  - PK = (user_id, symbol). Live trades are not TF-segmented today (live
    runs only the 1h primary lane); shadow needed (sym, tf) because both
    1h and 15m lanes run simultaneously. PR8 keeps the simpler PK; if
    live ever spawns a 15m lane, a future migration extends the PK.
  - `cooldown_until`: TIMESTAMPTZ. The dispatcher gate compares against
    `datetime.now(UTC)`.
  - `last_exit_reason`: TEXT NOT NULL. Enum values defined in
    app/db/live_exit_reasons.py — TEXT (vs CHECK constraint) keeps the
    enum forward-compatible without migrations.
  - `last_mtf_agreement`: SMALLINT NULL. Nullable because pre-PR1 trades
    don't carry mtf_agreement; PR2 backfilled current schema but
    historic rows may still be null.
  - `updated_at`: TIMESTAMPTZ, server-default NOW(). Audit trail for
    when a given cooldown was last touched.

The partial index `ix_live_cooldowns_active` accelerates the
`/bot-status/cooldowns` endpoint (only active cooldowns) and is Postgres-
only (SQLite doesn't support partial indexes the same way). The
dispatcher's PK lookup is already indexed via the PRIMARY KEY constraint.

Downgrade is fully reversible — DROP INDEX then DROP TABLE. The round
trip is exercised by tests/db/test_pr8_migration_downgrade.py per
FU-10 anticipation.

Revision ID: 0022_pr8_live_cooldowns
Revises: 0021_pr3_shadow_per_tf
Create Date: 2026-05-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0022_pr8_live_cooldowns"
down_revision: str | None = "0021_pr3_shadow_per_tf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_cooldowns",
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_exit_reason", sa.Text, nullable=False),
        sa.Column("last_mtf_agreement", sa.SmallInteger, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id", "symbol", name="live_cooldowns_pkey"),
    )

    # Partial active-only index (Postgres only — SQLite uses different
    # syntax and the test suite doesn't rely on this index existing).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_live_cooldowns_active "
            "ON live_cooldowns (cooldown_until) "
            "WHERE cooldown_until > NOW()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_live_cooldowns_active")
    op.drop_table("live_cooldowns")
