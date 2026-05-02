"""initial sp-0 schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.execute(
        """
        CREATE TABLE ohlcv (
            symbol     TEXT             NOT NULL,
            timeframe  TEXT             NOT NULL,
            ts         TIMESTAMPTZ      NOT NULL,
            open       DOUBLE PRECISION NOT NULL,
            high       DOUBLE PRECISION NOT NULL,
            low        DOUBLE PRECISION NOT NULL,
            close      DOUBLE PRECISION NOT NULL,
            volume     DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (symbol, timeframe, ts)
        );
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'ohlcv', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        );
        """
    )
    op.execute(
        """
        ALTER TABLE ohlcv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol,timeframe',
            timescaledb.compress_orderby   = 'ts'
        );
        """
    )
    op.execute(
        "SELECT add_compression_policy('ohlcv', INTERVAL '30 days', if_not_exists => TRUE);"
    )

    op.execute(
        """
        CREATE TABLE watchlist (
            id                   BIGSERIAL PRIMARY KEY,
            symbol               TEXT NOT NULL UNIQUE,
            is_favorite          BOOLEAN NOT NULL DEFAULT FALSE,
            paper_trade_active   BOOLEAN NOT NULL DEFAULT FALSE,
            added_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS watchlist;")
    op.execute("DROP TABLE IF EXISTS ohlcv;")
