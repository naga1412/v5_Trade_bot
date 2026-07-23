"""Healer Phase 0 — healer_findings + healer_known_error_types

Two operational tables (NOT hash-chained — these are observability data,
not audit-critical facts):

  * healer_findings: append-only log of every detector hit. The
    healer-status ops-debug probe reads this to summarize current
    faults + last-24h detections.

  * healer_known_error_types: registry of dispatch-error exception types
    the operator has previously seen. C1 alarms on any NEVER-BEFORE-SEEN
    class here, so a new exception surface (e.g. a new asyncpg bug class)
    can't hide behind the >5/hr rate limit.

Revision ID: 0030_healer_findings
Revises: 0029_live_trades_approved_via
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0030_healer_findings"
down_revision: str | None = "0029_live_trades_approved_via"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE healer_findings (
            id BIGSERIAL PRIMARY KEY,
            detector_name TEXT NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            severity TEXT NOT NULL
                CHECK (severity IN ('info', 'warning', 'critical')),
            summary TEXT NOT NULL,
            details JSONB
        );
        """
    )
    op.execute(
        "CREATE INDEX healer_findings_recent_idx "
        "ON healer_findings (detector_name, detected_at DESC);"
    )
    op.execute(
        """
        CREATE TABLE healer_known_error_types (
            error_type TEXT PRIMARY KEY,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            seen_count BIGINT NOT NULL DEFAULT 1
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS healer_known_error_types;")
    op.execute("DROP INDEX IF EXISTS healer_findings_recent_idx;")
    op.execute("DROP TABLE IF EXISTS healer_findings;")
