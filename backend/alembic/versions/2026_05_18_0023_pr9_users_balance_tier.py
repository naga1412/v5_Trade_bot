"""PR9: users.balance_tier — Kelly-fractional sizing tier classification.

Per the PR9 design spec (docs/superpowers/specs/2026-05-18-pr9-dynamic-
sizing-self-healing-design.md §6), this migration adds a cached tier
column to `users`. The dispatcher's dynamic-sizing path reads the tier
to look up the per-tier max-fraction cap; caching avoids re-classifying
on every signal.

Schema rationale:
  - 4 buckets: 'small' (<$1k), 'medium' ($1k-$10k), 'large' ($10k-$100k),
    'whale' ($100k+).
  - Stored as TEXT (vs CHECK constraint) — forward-compatible with future
    tier additions without migrations. Validation lives in
    app/trading/dynamic_sizing.py.
  - DEFAULT 'small' is the safest baseline — sizing-by-tier caps at 1%
    of bankroll, which is correct for an unknown/new user. Existing
    users get backfilled to 'small'; build_user_context re-classifies
    them lazily on next dispatch.

Standard 4-step add/backfill/NOT-NULL/DEFAULT pattern (PR1's live_trades
.timeframe pattern). Reversible downgrade tested in
tests/db/test_pr9_migration_downgrade.py.

Revision ID: 0023_pr9_users_balance_tier
Revises: 0022_pr8_live_cooldowns
Create Date: 2026-05-18
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0023_pr9_users_balance_tier"
down_revision: str | None = "0022_pr8_live_cooldowns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Step 1: add column nullable so the backfill can run.
    op.execute("ALTER TABLE users ADD COLUMN balance_tier VARCHAR(16) NULL;")
    # Step 2: backfill — all existing users start at 'small'.
    op.execute("UPDATE users SET balance_tier = 'small' WHERE balance_tier IS NULL;")
    if is_pg:
        # Step 3: tighten to NOT NULL now that every row has a value.
        op.execute("ALTER TABLE users ALTER COLUMN balance_tier SET NOT NULL;")
        # Step 4: server-side DEFAULT so new inserts that omit the column
        # land in the safest bucket.
        op.execute("ALTER TABLE users ALTER COLUMN balance_tier SET DEFAULT 'small';")
    # SQLite: ALTER COLUMN unsupported; backfill guarantees all rows have
    # a value, app-layer ensures new rows supply one.


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN balance_tier;")
