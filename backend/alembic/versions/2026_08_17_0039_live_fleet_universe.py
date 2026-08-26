"""live_fleet_universe -- Phase 4 liquidity-floor selector snapshots

One row per (symbol, snapshot_at) -- mirrors asset_universe's own
snapshot-keyed shape (same "latest snapshot_at wins" read pattern), but
this is a SEPARATE table, not a repurposing of asset_universe: this one
is liquidity-floor-selected across the full market for the live-fleet
(WS + REST-poll) supervisors specifically; asset_universe stays exactly
as-is (spot-top-30 by volume, still used by shadow_worker's own
universe). Do not merge these two tables -- see the liquidity-floor-
selector decision record's "open dependency question" for why they can
legitimately diverge (a symbol can be liquidity-floor-qualified via deep
FUTURES liquidity while ranking outside the spot-volume top-30).

Revision ID: 0039_live_fleet_universe
Revises: 0038_symbol_source_tag
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0039_live_fleet_universe"
down_revision: str | None = "0038_symbol_source_tag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"
    ts_default = "DEFAULT now()" if is_pg else "DEFAULT CURRENT_TIMESTAMP"

    op.execute(f"""
        CREATE TABLE live_fleet_universe (
            symbol VARCHAR(20) NOT NULL,
            cohort TEXT NOT NULL,
            qvol_24h DOUBLE PRECISION NOT NULL,
            spread_bps DOUBLE PRECISION NOT NULL,
            depth_0_5pct_usdt DOUBLE PRECISION NOT NULL,
            snapshot_at {ts_type} NOT NULL {ts_default},
            PRIMARY KEY (symbol, snapshot_at)
        );
    """)
    op.execute(
        "CREATE INDEX live_fleet_universe_snapshot_idx "
        "ON live_fleet_universe (snapshot_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS live_fleet_universe_snapshot_idx;")
    op.execute("DROP TABLE IF EXISTS live_fleet_universe;")
