"""audit hash chain for predictions and paper_trades

Revision ID: 0002_audit_chain
Revises: 0001_initial
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002_audit_chain"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE predictions (
            id                BIGSERIAL PRIMARY KEY,
            symbol            TEXT NOT NULL,
            timeframe         TEXT NOT NULL,
            ts                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            layer_scores      JSONB NOT NULL,
            final_score       DOUBLE PRECISION NOT NULL,
            direction         TEXT,
            confidence        DOUBLE PRECISION,
            inputs_hash       TEXT NOT NULL,
            model_version     TEXT NOT NULL DEFAULT 'sp-0',
            cold_start        BOOLEAN NOT NULL DEFAULT TRUE,
            prev_hash         TEXT NOT NULL,
            row_hash          TEXT NOT NULL UNIQUE
        )
        """
    )
    op.execute("CREATE INDEX predictions_symbol_ts_idx ON predictions (symbol, ts DESC)")

    op.execute(
        """
        CREATE TABLE paper_trades (
            id                       BIGSERIAL PRIMARY KEY,
            symbol                   TEXT NOT NULL,
            direction                TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
            entry_price              DOUBLE PRECISION NOT NULL,
            exit_price               DOUBLE PRECISION,
            stop_loss                DOUBLE PRECISION NOT NULL,
            take_profit              DOUBLE PRECISION NOT NULL,
            position_size            DOUBLE PRECISION NOT NULL,
            opened_at                TIMESTAMPTZ NOT NULL,
            closed_at                TIMESTAMPTZ,
            pnl_pct                  DOUBLE PRECISION,
            max_drawdown_during      DOUBLE PRECISION,
            bars_held                INTEGER,
            exit_reason              TEXT,
            reasoning                JSONB,
            model_version            TEXT NOT NULL DEFAULT 'sp-0',
            prev_hash                TEXT NOT NULL,
            row_hash                 TEXT NOT NULL UNIQUE
        )
        """
    )
    op.execute(
        "CREATE INDEX paper_trades_symbol_opened_idx ON paper_trades (symbol, opened_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE audit_violations (
            id          BIGSERIAL PRIMARY KEY,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            table_name  TEXT NOT NULL,
            row_id      BIGINT NOT NULL,
            expected    TEXT NOT NULL,
            actual      TEXT NOT NULL,
            note        TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE data_quality_alerts (
            id          BIGSERIAL PRIMARY KEY,
            ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            candle_ts   TIMESTAMPTZ NOT NULL,
            check_name  TEXT NOT NULL,
            details     JSONB NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX dqa_symbol_ts_idx ON data_quality_alerts (symbol, ts DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_quality_alerts;")
    op.execute("DROP TABLE IF EXISTS audit_violations;")
    op.execute("DROP TABLE IF EXISTS paper_trades;")
    op.execute("DROP TABLE IF EXISTS predictions;")
