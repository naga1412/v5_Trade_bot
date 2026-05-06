"""intermarket_snapshots table for SP-3.5 OI / Funding Rate

Revision ID: 0014_intermarket
Revises: 0013_news_items
Create Date: 2026-05-06
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0014_intermarket"
down_revision: str | None = "0013_news_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE intermarket_snapshots (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                funding_rate DOUBLE PRECISION,
                mark_price DOUBLE PRECISION,
                open_interest DOUBLE PRECISION,
                source TEXT NOT NULL
                    CHECK (source IN ('binance_futures', 'bybit'))
            );
            """
        )
        op.execute(
            "CREATE INDEX intermarket_snapshots_symbol_ts_idx "
            "ON intermarket_snapshots (symbol, captured_at DESC);"
        )
    else:
        # SQLite mirror — TEXT timestamps, REAL numerics. The DEFAULT
        # mirrors the Postgres NOW() so callers that rely on it for
        # cleanup-cutoff math behave identically across dialects.
        op.execute(
            """
            CREATE TABLE intermarket_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT (datetime('now')),
                funding_rate REAL,
                mark_price REAL,
                open_interest REAL,
                source TEXT NOT NULL
                    CHECK (source IN ('binance_futures', 'bybit'))
            );
            """
        )
        op.execute(
            "CREATE INDEX intermarket_snapshots_symbol_ts_idx "
            "ON intermarket_snapshots (symbol, captured_at DESC);"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS intermarket_snapshots_symbol_ts_idx;")
    op.execute("DROP TABLE IF EXISTS intermarket_snapshots;")
