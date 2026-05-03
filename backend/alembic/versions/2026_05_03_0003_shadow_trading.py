"""shadow trading tables (asset_universe, shadow_trades, shadow_open_positions, shadow_cooldowns)

Revision ID: 0003_shadow_trading
Revises: 0002_audit_chain
Create Date: 2026-05-03
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0003_shadow_trading"
down_revision: Union[str, None] = "0002_audit_chain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE asset_universe (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            quote_volume_usd_24h DOUBLE PRECISION NOT NULL,
            rank INTEGER NOT NULL,
            snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (symbol, snapshot_at)
        );
        """
    )
    op.execute(
        "CREATE INDEX asset_universe_snapshot_idx ON asset_universe (snapshot_at DESC, rank);"
    )

    op.execute(
        """
        CREATE TABLE shadow_trades (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1h',
            direction TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
            entry_price DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            take_profit DOUBLE PRECISION NOT NULL,
            position_size_usdt DOUBLE PRECISION NOT NULL,
            entry_score DOUBLE PRECISION NOT NULL,
            entry_confidence DOUBLE PRECISION NOT NULL,
            layer_scores JSONB NOT NULL,
            entry_atr DOUBLE PRECISION NOT NULL,
            exit_price DOUBLE PRECISION,
            exit_reason TEXT CHECK (exit_reason IN ('TAKE_PROFIT','STOP_LOSS','TIMEOUT')),
            pnl_pct DOUBLE PRECISION,
            pnl_usdt DOUBLE PRECISION,
            bars_held INTEGER,
            opened_at TIMESTAMPTZ NOT NULL,
            closed_at TIMESTAMPTZ,
            inputs_hash TEXT NOT NULL,
            model_version TEXT NOT NULL DEFAULT 'sp-0.5',
            signal_id TEXT NOT NULL UNIQUE,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL UNIQUE
        );
        """
    )
    op.execute(
        "CREATE INDEX shadow_trades_symbol_opened_idx ON shadow_trades (symbol, opened_at DESC);"
    )
    op.execute(
        "CREATE INDEX shadow_trades_closed_idx ON shadow_trades (closed_at DESC) WHERE closed_at IS NOT NULL;"
    )

    op.execute(
        """
        CREATE TABLE shadow_open_positions (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE,
            direction TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
            entry_price DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            take_profit DOUBLE PRECISION NOT NULL,
            position_size_usdt DOUBLE PRECISION NOT NULL,
            entry_score DOUBLE PRECISION NOT NULL,
            entry_confidence DOUBLE PRECISION NOT NULL,
            entry_atr DOUBLE PRECISION NOT NULL,
            bars_held INTEGER NOT NULL DEFAULT 0,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            signal_id TEXT NOT NULL UNIQUE
        );
        """
    )

    op.execute(
        """
        CREATE TABLE shadow_cooldowns (
            symbol TEXT PRIMARY KEY,
            cooldown_until TIMESTAMPTZ NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shadow_cooldowns;")
    op.execute("DROP TABLE IF EXISTS shadow_open_positions;")
    op.execute("DROP TABLE IF EXISTS shadow_trades;")
    op.execute("DROP TABLE IF EXISTS asset_universe;")
