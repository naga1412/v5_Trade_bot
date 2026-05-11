"""worker_heartbeats — per-worker liveness signal for the watchdog

Revision ID: 0017_worker_heartbeats
Revises: 0016_sp8_trading_tables
Create Date: 2026-05-11

Each background worker calls record_heartbeat(name) on every loop tick.
The watchdog reads MAX(beat_at) per worker_name and alerts when the
delta exceeds the worker's expected cadence. ON CONFLICT DO UPDATE
gives us a single row per worker, kept fresh — no GC needed.
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0017_worker_heartbeats"
down_revision: str | None = "0016_sp8_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE worker_heartbeats (
            worker_name TEXT PRIMARY KEY,
            beat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_status TEXT NOT NULL DEFAULT 'ok',
            details JSONB
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS worker_heartbeats;")
