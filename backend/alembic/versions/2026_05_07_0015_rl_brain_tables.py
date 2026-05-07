"""rl_checkpoints + brain_decisions tables for SP-4 Phase A1

Revision ID: 0015_rl_brain
Revises: 0014_intermarket
Create Date: 2026-05-07

Adds two tables:

* ``rl_checkpoints`` — registry + active flag for trained PPO policy
  checkpoints. Mirrors the SP-1 ``ml_checkpoints`` shape (single-active-
  per-model invariant via partial unique index on Postgres; tests
  manually construct tables on SQLite).
* ``brain_decisions`` — append-only log of every brain action emitted in
  production. Hash-chained via ``prev_hash``/``row_hash`` so SP-7's
  ``verify_chain`` worker can pick it up alongside ``predictions`` and
  ``shadow_trades``.

Cross-cutting compliance: SP-4 spec sec 8 — same audit policy as
existing chained tables; same dialect-aware DDL as migration 0014.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0015_rl_brain"
down_revision: str | None = "0014_intermarket"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE rl_checkpoints (
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
        op.execute(
            "CREATE UNIQUE INDEX rl_checkpoints_one_active_idx "
            "ON rl_checkpoints (model_name) WHERE is_active = TRUE;"
        )
        op.execute(
            """
            CREATE TABLE brain_decisions (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                checkpoint_id BIGINT NOT NULL
                    REFERENCES rl_checkpoints(id),
                observation JSONB NOT NULL,
                action TEXT NOT NULL
                    CHECK (action IN ('LONG_FULL','LONG_HALF','FLAT',
                                      'SHORT_HALF','SHORT_FULL')),
                action_logits JSONB NOT NULL,
                value_estimate DOUBLE PRECISION,
                smoothed_action TEXT NOT NULL
                    CHECK (smoothed_action IN ('LONG_FULL','LONG_HALF','FLAT',
                                               'SHORT_HALF','SHORT_FULL')),
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            """
        )
        op.execute(
            "CREATE INDEX brain_decisions_symbol_ts_idx "
            "ON brain_decisions (symbol, ts DESC);"
        )
    else:
        # SQLite mirror — TEXT timestamps, JSON-as-TEXT, no partial unique
        # index (the at-most-one-active invariant is enforced at the app
        # layer; tests construct tables manually anyway).
        op.execute(
            """
            CREATE TABLE rl_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                version TEXT NOT NULL,
                checkpoint_uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                trained_at TEXT NOT NULL,
                train_data_window TEXT NOT NULL,
                eval_results TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                activated_at TEXT,
                deactivated_at TEXT,
                notes TEXT,
                UNIQUE (model_name, version)
            );
            """
        )
        op.execute(
            """
            CREATE TABLE brain_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                checkpoint_id INTEGER NOT NULL
                    REFERENCES rl_checkpoints(id),
                observation TEXT NOT NULL,
                action TEXT NOT NULL
                    CHECK (action IN ('LONG_FULL','LONG_HALF','FLAT',
                                      'SHORT_HALF','SHORT_FULL')),
                action_logits TEXT NOT NULL,
                value_estimate REAL,
                smoothed_action TEXT NOT NULL
                    CHECK (smoothed_action IN ('LONG_FULL','LONG_HALF','FLAT',
                                               'SHORT_HALF','SHORT_FULL')),
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL
            );
            """
        )
        op.execute(
            "CREATE INDEX brain_decisions_symbol_ts_idx "
            "ON brain_decisions (symbol, ts DESC);"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS brain_decisions_symbol_ts_idx;")
    op.execute("DROP TABLE IF EXISTS brain_decisions;")
    op.execute("DROP INDEX IF EXISTS rl_checkpoints_one_active_idx;")
    op.execute("DROP TABLE IF EXISTS rl_checkpoints;")
