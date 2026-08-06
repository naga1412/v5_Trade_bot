"""flow_feature_snapshots — continuous time series for W4 order-flow features

Leading-indicators item 3 (2026-08-06): `app.core.features.flow_features`
already fetches ls_account_ratio / taker_buy_sell_ratio / oi_4h_delta from
Binance Futures REST on every 1h shadow-worker candle across the full
universe, but the result only ever lived in an in-process cache
(`_FLOW_CACHE`, lost on restart) and got durably persisted exactly once
per symbol at shadow-trade-OPEN time (`shadow_observations.components`,
record-only, sparse). This table gives the same already-fetched values a
continuous per-symbol time series, at zero new API cost — no new fetch,
just a new row on every existing refresh. Storage only; not read by any
scoring or gating path.

Revision ID: 0032_flow_feature_snapshots
Revises: 0031_shadow_trade_variants
Create Date: 2026-08-06
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0032_flow_feature_snapshots"
down_revision: str | None = "0031_shadow_trade_variants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE flow_feature_snapshots (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ls_account_ratio DOUBLE PRECISION,
            taker_buy_sell_ratio DOUBLE PRECISION,
            oi_4h_delta DOUBLE PRECISION,
            source TEXT NOT NULL DEFAULT 'binance_futures'
        );
        """
    )
    op.execute(
        "CREATE INDEX flow_feature_snapshots_symbol_ts_idx "
        "ON flow_feature_snapshots (symbol, captured_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS flow_feature_snapshots_symbol_ts_idx;")
    op.execute("DROP TABLE IF EXISTS flow_feature_snapshots;")
