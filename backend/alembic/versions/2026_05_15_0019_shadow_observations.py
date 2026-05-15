"""shadow_observations — full RL observation snapshot at trade-open time

Revision ID: 0019_shadow_observations
Revises: 0018_prediction_validations
Create Date: 2026-05-15

Stores the exact components of the brain's 58-float observation at the
moment a shadow_open_position is created. The `app.rl.replay_buffer`
loader uses this to feed PPO training with EXACT obs values instead of
lossy time-based JOINs against intermarket_snapshots and regime tables.

Separate table (not columns on shadow_trades) because shadow_trades is
hash-chained — see migration 0018 prediction_validations for the same
pattern. JOIN by signal_id (which survives the move from
shadow_open_positions → shadow_trades).

Stored as a JSONB blob of per-component values rather than a pre-
assembled float array. That way if the obs layout ever changes
(e.g. extra macro feature added), the loader can still re-assemble
from stored components without backfilling old rows.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0019_shadow_observations"
down_revision: str | None = "0018_prediction_validations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    is_pg = dialect.startswith("postgres")

    jsonb = "JSONB" if is_pg else "TEXT"
    ts_default = "TIMESTAMPTZ NOT NULL DEFAULT NOW()" if is_pg else (
        "TEXT NOT NULL DEFAULT (datetime('now'))"
    )
    pk = "BIGSERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts_type = "TIMESTAMPTZ NOT NULL" if is_pg else "TEXT NOT NULL"

    op.execute(
        f"""
        CREATE TABLE shadow_observations (
            id {pk},
            signal_id TEXT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            symbol TEXT NOT NULL,
            opened_at {ts_type},
            components {jsonb} NOT NULL,
            created_at {ts_default}
        );
        """
    )
    op.execute(
        "CREATE INDEX shadow_observations_signal_id_idx "
        "ON shadow_observations (signal_id);"
    )
    op.execute(
        "CREATE INDEX shadow_observations_user_opened_idx "
        "ON shadow_observations (user_id, opened_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shadow_observations_user_opened_idx;")
    op.execute("DROP INDEX IF EXISTS shadow_observations_signal_id_idx;")
    op.execute("DROP TABLE IF EXISTS shadow_observations;")
