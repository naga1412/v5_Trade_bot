"""live_prediction_watermarks -- Phase 4 idempotency for the futures REST poller

The REST poller can re-observe the same closed candle after a restart, a
clock-skew tick, or an overlapping poll. This table is the persisted
watermark that guarantees a given (symbol, timeframe, candle open-time)
is processed at most once, independent of the hash-chained predictions
table's schema.

Revision ID: 0037_live_pred_watermarks
Revises: 0036_deactivate_rl_ckpt_62
Create Date: 2026-08-17
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0037_live_pred_watermarks"
down_revision: str | None = "0036_deactivate_rl_ckpt_62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")
    ts_type = "TIMESTAMPTZ" if is_pg else "TEXT"

    op.execute(f"""
        CREATE TABLE live_prediction_watermarks (
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(8) NOT NULL,
            last_open_time BIGINT NOT NULL,
            updated_at {ts_type} NOT NULL {"DEFAULT now()" if is_pg else "DEFAULT CURRENT_TIMESTAMP"},
            PRIMARY KEY (symbol, timeframe)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_prediction_watermarks;")
