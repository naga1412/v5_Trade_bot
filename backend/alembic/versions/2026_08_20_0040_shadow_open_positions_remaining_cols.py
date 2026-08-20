"""Fix restart-loss: 5 remaining shadow_trades columns never added to
shadow_open_positions (2026-08-20 pattern-layer investigation follow-up).

PR-PLUMBING-1 Fix 3 (migration 0025) added 7 PR1 analytics columns to
shadow_open_positions so those specific fields survive a worker restart
between a position's open and close. Verified directly this session
(not from comments) that Fix 3 correctly covers: mtf_agreement,
mtf_dominant_tf, mtf_directions_json, p_win, effective_score,
realized_vol_20d, funding_directional_adj -- all 7 round-trip correctly
today.

Five OTHER shadow_trades columns were never added, because they either
predate migration 0020 (layer_scores) or were added to shadow_trades
AFTER migration 0025 shipped without a matching shadow_open_positions
follow-up (mtf_adx_by_tf_json: migration 0034, 2026-08-13; symbol_source:
migration 0038, 2026-08-17; hold_scaling_factor/hold_timeout_bars:
migration 0021, 2026-05-18, predates 0025 by 3 days but was missed):

  - layer_scores            JSONB / TEXT, NULL (shadow_trades: NOT NULL,
                             but a retrofit on an existing table can't add
                             NOT NULL without a default; app-layer treats
                             NULL the same as {} on read)
  - mtf_adx_by_tf_json       JSONB / TEXT, NULL
  - symbol_source            TEXT, NOT NULL DEFAULT 'established_top20'
                             (matches shadow_trades' own default exactly)
  - hold_scaling_factor      REAL, NULL
  - hold_timeout_bars        SMALLINT, NULL

Confirmed via real data this session: a restart-survivor position closes
with layer_scores={} (empty JSON object) on the resulting shadow_trades
row -- 48 of 289 trades (16.6%) in a 90d LONG/atr_bound/top-20 sample.
Same class of loss for the other 4 columns, just silently null instead
of an empty-but-present container.

No backfill for existing rows -- same operator directive as migration
0025 ("no backfill -- existing rows stay NULL until they close"). This
column addition only protects positions that are STILL OPEN as of this
migration and close AFTER it; already-closed trades with layer_scores={}
keep that value (pattern_stats' own backfill, migration-adjacent PR #507,
is a separate concern and does not touch shadow_trades rows directly).

Revision ID: 0040_shadow_open_positions_remaining_cols
Revises: 0039_live_fleet_universe
Create Date: 2026-08-20
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0040_shadow_open_positions_remaining_cols"
down_revision: str | None = "0039_live_fleet_universe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    json_type = "JSONB" if is_pg else "TEXT"

    op.execute(
        f"ALTER TABLE shadow_open_positions ADD COLUMN layer_scores {json_type} NULL;"
    )
    op.execute(
        f"ALTER TABLE shadow_open_positions ADD COLUMN mtf_adx_by_tf_json {json_type} NULL;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD COLUMN symbol_source TEXT NOT NULL DEFAULT 'established_top20';"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions ADD COLUMN hold_scaling_factor REAL NULL;"
    )
    op.execute(
        "ALTER TABLE shadow_open_positions ADD COLUMN hold_timeout_bars SMALLINT NULL;"
    )


def downgrade() -> None:
    for col_name in (
        "layer_scores", "mtf_adx_by_tf_json", "symbol_source",
        "hold_scaling_factor", "hold_timeout_bars",
    ):
        op.execute(
            f"ALTER TABLE shadow_open_positions DROP COLUMN IF EXISTS {col_name};"
        )
