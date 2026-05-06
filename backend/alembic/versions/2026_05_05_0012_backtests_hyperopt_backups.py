"""backtests + hyperopt_studies + backup_runs (SP-7 Phase A2)

Revision ID: 0012_backtests_hyperopt_backups
Revises: 0011_trap_enabled
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0012_backtests_hyperopt_backups"
down_revision: str | None = "0011_trap_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -------- backtests --------
    op.execute(
        """
        CREATE TABLE backtests (
            id BIGSERIAL PRIMARY KEY,
            triggered_by BIGINT REFERENCES users(id),
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            start_ts TIMESTAMPTZ NOT NULL,
            end_ts TIMESTAMPTZ NOT NULL,
            layer_weights JSONB,
            enabled_layers JSONB,
            enabled_traps JSONB,
            initial_balance DOUBLE PRECISION NOT NULL,
            n_trades INTEGER NOT NULL DEFAULT 0,
            win_rate DOUBLE PRECISION,
            profit_factor DOUBLE PRECISION,
            sharpe DOUBLE PRECISION,
            max_drawdown DOUBLE PRECISION,
            equity_curve_uri TEXT,
            trade_log_uri TEXT,
            params_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed'
                CHECK (status IN ('running','completed','failed')),
            error_message TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX backtests_symbol_tf_idx "
        "ON backtests (symbol, timeframe, triggered_at DESC);"
    )
    op.execute(
        "CREATE INDEX backtests_params_hash_idx "
        "ON backtests (params_hash);"
    )

    # -------- hyperopt_studies --------
    op.execute(
        """
        CREATE TABLE hyperopt_studies (
            id BIGSERIAL PRIMARY KEY,
            triggered_by BIGINT REFERENCES users(id),
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            n_trials INTEGER NOT NULL,
            train_window TSTZRANGE NOT NULL,
            val_window TSTZRANGE NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            best_weights JSONB,
            best_sharpe DOUBLE PRECISION,
            mlflow_run_id TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('running','completed','failed')),
            error_message TEXT
        );
        """
    )
    op.execute(
        "CREATE INDEX hyperopt_studies_status_idx "
        "ON hyperopt_studies (status, triggered_at DESC);"
    )

    # -------- backup_runs --------
    op.execute(
        """
        CREATE TABLE backup_runs (
            id BIGSERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            backup_type TEXT NOT NULL
                CHECK (backup_type IN
                    ('hourly_dump','nightly_basebackup','recovery_rehearsal')),
            target TEXT NOT NULL,
            success BOOLEAN,
            size_bytes BIGINT,
            duration_seconds DOUBLE PRECISION,
            error_message TEXT,
            metadata_json JSONB
        );
        """
    )
    op.execute(
        "CREATE INDEX backup_runs_type_started_idx "
        "ON backup_runs (backup_type, started_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backup_runs_type_started_idx;")
    op.execute("DROP TABLE IF EXISTS backup_runs;")
    op.execute("DROP INDEX IF EXISTS hyperopt_studies_status_idx;")
    op.execute("DROP TABLE IF EXISTS hyperopt_studies;")
    op.execute("DROP INDEX IF EXISTS backtests_params_hash_idx;")
    op.execute("DROP INDEX IF EXISTS backtests_symbol_tf_idx;")
    op.execute("DROP TABLE IF EXISTS backtests;")
