"""symbol_source cohort tag -- Phase 4, three-way (2026-08-17 redraft)

Additive metadata column on predictions, shadow_trades, telegram_signals,
and live_trades so every downstream consumer (Telegram card, app view,
future reporting, AND the reversal-criterion-#2 safety re-measurement --
see the liquidity-floor-selector decision record) can split cohorts from
one source of truth. NOT part of any hash-chained payload's hashed
content on predictions/shadow_trades/live_trades -- existing rows keep
their existing row_hash values, matching the PR1 record-only column
precedent (telegram_signals is not hash-chained at all).

Values: 'established_top20' (default -- pre-cutover coverage, a lineage
tag assigned once and not recomputed), 'liquidity_added_spot' (spot-
backed, newly covered, the larger and unproven of the two new cohorts),
'futures_poll' (futures-only, unchanged name from the original two-value
scheme but now one of three, not one of two).

Revision ID: 0038_symbol_source_tag
Revises: 0037_live_pred_watermarks
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0038_symbol_source_tag"
down_revision: str | None = "0037_live_pred_watermarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("predictions", "shadow_trades", "telegram_signals", "live_trades")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN symbol_source TEXT NOT NULL DEFAULT 'established_top20';"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS symbol_source;")
