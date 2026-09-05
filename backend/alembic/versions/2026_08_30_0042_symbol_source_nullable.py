"""symbol_source nullable -- NULL-on-failure, never a guessed cohort (2026-08-30)

Item 0, constraint 3 (operator ruling, 2026-08-30): "If classification
cannot complete for any reason -- cache empty, session unavailable,
unexpected state -- write NULL, log at ERROR, and fire a healer alert.
NEVER fall back to a cohort value. A hardcoded fallback tag is
precisely the defect we just spent this entire week unwinding: the
rescue path's established_top20 default fabricated lineage for
TRUMPUSDT across 321 predictions and looked perfectly healthy the whole
time. A NULL is honest and greppable; a wrong tag is poison that
survives every audit."

Both `shadow_open_positions.symbol_source` (migration 0040) and
`shadow_trades.symbol_source` (migration 0038) currently carry
`NOT NULL DEFAULT 'established_top20'`. Under that constraint,
app.shadow.worker's new synchronous open-time classification (item 0)
could not write NULL even when it wanted to -- the INSERT would either
reject the NULL outright or (worse, if a caller ever fell back to the
column default instead of binding explicitly) silently write the exact
fabricated-lineage default this item exists to stop producing.

This migration drops NOT NULL and DROPs the DEFAULT on both columns.
No backfill: existing rows keep their current value (real cohort tags
for the overwhelming majority, since this only affects behavior going
forward from classification failures, which are expected to be rare).

Revision ID: 0042_symbol_source_nullable
Revises: 0041_cohort_baseline
Create Date: 2026-08-30
"""
from __future__ import annotations

from alembic import op

revision: str = "0042_symsrc_null"
down_revision: str | None = "0041_cohort_baseline"
branch_labels = None
depends_on = None

_TABLES = ("shadow_open_positions", "shadow_trades")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN symbol_source DROP NOT NULL;")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN symbol_source DROP DEFAULT;")


def downgrade() -> None:
    # Restore NOT NULL DEFAULT 'established_top20' -- matches migrations
    # 0038/0040's original definition. Any row written NULL under this
    # migration (a genuine classification-failure record) is silently
    # coerced back to 'established_top20' by the NOT NULL re-add unless
    # backfilled first; that data-loss risk is the same shape the
    # operator flagged for the original defect, so a downgrade of this
    # migration should not be run against a database with real NULL
    # rows without an explicit backfill decision first.
    for table in _TABLES:
        op.execute(
            f"UPDATE {table} SET symbol_source = 'established_top20' "
            f"WHERE symbol_source IS NULL;"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN symbol_source "
            f"SET DEFAULT 'established_top20';"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN symbol_source SET NOT NULL;")
