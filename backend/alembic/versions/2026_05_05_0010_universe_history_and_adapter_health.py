"""universe_history + adapter_health (SP-3 Phase A spec §4.1, §4.3)

Revision ID: 0010_universe_history_and_adapter_health
Revises: 0009_pattern_enabled
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0010_universe_adapter_health"
down_revision: str | None = "0009_pattern_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE universe_history (
            id BIGSERIAL PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL
                CHECK (asset_class IN ('crypto', 'stock', 'fx', 'commodity', 'index')),
            listed_at TIMESTAMPTZ NOT NULL,
            delisted_at TIMESTAMPTZ,
            last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata JSONB,
            UNIQUE (exchange, symbol)
        );
        """
    )
    op.execute(
        "CREATE INDEX universe_history_exchange_active_idx "
        "ON universe_history (exchange) WHERE delisted_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX universe_history_symbol_idx "
        "ON universe_history (symbol);"
    )

    op.execute(
        """
        CREATE TABLE adapter_health (
            id BIGSERIAL PRIMARY KEY,
            exchange TEXT NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_healthy BOOLEAN NOT NULL,
            latency_ms INTEGER,
            error_message TEXT,
            quota_used_pct DOUBLE PRECISION
        );
        """
    )
    op.execute(
        "CREATE INDEX adapter_health_recent_idx "
        "ON adapter_health (exchange, checked_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS adapter_health_recent_idx;")
    op.execute("DROP TABLE IF EXISTS adapter_health;")
    op.execute("DROP INDEX IF EXISTS universe_history_symbol_idx;")
    op.execute("DROP INDEX IF EXISTS universe_history_exchange_active_idx;")
    op.execute("DROP TABLE IF EXISTS universe_history;")
