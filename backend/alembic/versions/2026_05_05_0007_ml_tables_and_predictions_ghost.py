"""ML tables (feature_registry, ml_checkpoints, pattern_stats) + ghost columns on predictions

Revision ID: 0007_ml_tables_and_predictions_ghost
Revises: 0006_user_id_not_null
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0007_ml_tables_ghost"
down_revision: str | None = "0006_user_id_not_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) ml_checkpoints — registry + active flag
    op.execute(
        """
        CREATE TABLE ml_checkpoints (
            id BIGSERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            version TEXT NOT NULL,
            checkpoint_uri TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            trained_at TIMESTAMPTZ NOT NULL,
            train_data_window TEXT NOT NULL,
            eval_results JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            activated_at TIMESTAMPTZ,
            deactivated_at TIMESTAMPTZ,
            notes TEXT,
            UNIQUE (model_name, version)
        );
        """
    )
    # Partial unique index — at most one active checkpoint per model. Postgres
    # supports `WHERE is_active = TRUE`; SQLite (test fixtures) does not run
    # alembic upgrade against this exact DDL — unit tests using SQLite create
    # tables manually without the partial index (see tests/unit/test_*).
    op.execute(
        "CREATE UNIQUE INDEX ml_checkpoints_active_idx "
        "ON ml_checkpoints (model_name) WHERE is_active = TRUE;"
    )

    # 2) feature_registry
    op.execute(
        """
        CREATE TABLE feature_registry (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL DEFAULT 1,
            description TEXT NOT NULL,
            dtype TEXT NOT NULL CHECK (dtype IN ('float', 'int', 'bool', 'category')),
            layer INTEGER,
            computation TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    # 3) pattern_stats — generated accuracy column
    op.execute(
        """
        CREATE TABLE pattern_stats (
            id BIGSERIAL PRIMARY KEY,
            pattern_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            n_samples INTEGER NOT NULL DEFAULT 0,
            n_correct INTEGER NOT NULL DEFAULT 0,
            accuracy DOUBLE PRECISION GENERATED ALWAYS AS
                (CASE WHEN n_samples = 0 THEN 0.5
                      ELSE n_correct::double precision / n_samples END) STORED,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (pattern_id, symbol, timeframe)
        );
        """
    )
    op.execute(
        "CREATE INDEX pattern_stats_symbol_tf_idx "
        "ON pattern_stats (symbol, timeframe);"
    )

    # 4) ghost columns on predictions
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_open DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_high DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_low DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_close DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_p5_low DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_p95_high DOUBLE PRECISION;")
    op.execute("ALTER TABLE predictions ADD COLUMN ghost_uncertainty DOUBLE PRECISION;")
    op.execute(
        "ALTER TABLE predictions ADD COLUMN model_checkpoint_id BIGINT "
        "REFERENCES ml_checkpoints(id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS model_checkpoint_id;")
    for col in (
        "ghost_uncertainty",
        "ghost_p95_high",
        "ghost_p5_low",
        "ghost_close",
        "ghost_low",
        "ghost_high",
        "ghost_open",
    ):
        op.execute(f"ALTER TABLE predictions DROP COLUMN IF EXISTS {col};")
    op.execute("DROP TABLE IF EXISTS pattern_stats;")
    op.execute("DROP TABLE IF EXISTS feature_registry;")
    op.execute("DROP INDEX IF EXISTS ml_checkpoints_active_idx;")
    op.execute("DROP TABLE IF EXISTS ml_checkpoints;")
