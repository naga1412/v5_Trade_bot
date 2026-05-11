"""prediction_validations — per-bar honest accuracy telemetry

Revision ID: 0018_prediction_validations
Revises: 0017_worker_heartbeats
Create Date: 2026-05-12

Each prediction inserted into ``predictions`` also gets a row here at
prediction time, with ``anchor_close`` captured but ``target_close``
and ``was_correct`` NULL. A background worker (``app/ml/validator.py``)
fills those columns when the target bar's actual close becomes
available. Endpoint ``GET /api/v1/predictions/accuracy`` aggregates over
the validated rows to surface rolling hit-rate by symbol/timeframe.

Separate table (not columns on ``predictions``) because ``predictions``
is hash-chained — appending columns later would invalidate the chain.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0018_prediction_validations"
down_revision: str | None = "0017_worker_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE prediction_validations (
            id BIGSERIAL PRIMARY KEY,
            prediction_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            anchor_ts TIMESTAMPTZ NOT NULL,
            anchor_close DOUBLE PRECISION NOT NULL,
            target_ts TIMESTAMPTZ NOT NULL,
            target_close DOUBLE PRECISION,
            was_correct BOOLEAN,
            pnl_pct DOUBLE PRECISION,
            validated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX prediction_validations_pending_idx "
        "ON prediction_validations (target_ts) "
        "WHERE validated_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX prediction_validations_lookup_idx "
        "ON prediction_validations (user_id, symbol, timeframe, anchor_ts DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS prediction_validations_lookup_idx;")
    op.execute("DROP INDEX IF EXISTS prediction_validations_pending_idx;")
    op.execute("DROP TABLE IF EXISTS prediction_validations;")
