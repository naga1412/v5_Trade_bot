"""Sync Postgres helper for writing backup_runs rows from CLI scripts.

Phase E5. Uses psycopg2 (Alembic transitive dep) — keep it sync; the backup
CLIs are invoked from cron, not from the FastAPI event loop.

Failures here are silently logged — we never want a metrics-write failure to
mask a successful backup or escalate a real failure on the operator's pager.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger(__name__)

BackupType = Literal["hourly_dump", "nightly_basebackup", "recovery_rehearsal"]


def record_backup_run(
    *,
    backup_type: BackupType,
    target: str,
    success: bool,
    size_bytes: int | None = None,
    duration_seconds: float | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> int | None:
    """INSERT a backup_runs row. Returns the new id, or ``None`` on any failure.

    Connection params from env: ``BACKUP_PGHOST``, ``BACKUP_PGPORT``,
    ``BACKUP_PGUSER``, ``BACKUP_PGPASSWORD``, ``POSTGRES_DB``.
    """
    try:
        import psycopg2  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        log.error("psycopg2 not installed; cannot record backup_run")
        return None

    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = int(os.environ.get("BACKUP_PGPORT", "5432"))
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "trading_radar")

    started = started_at or datetime.now(timezone.utc)
    completed = datetime.now(timezone.utc)
    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=pw, dbname=db,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO backup_runs "
                    "(started_at, completed_at, backup_type, target, success, "
                    "size_bytes, duration_seconds, error_message) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        started, completed, backup_type, target, success,
                        size_bytes, duration_seconds, error_message,
                    ),
                )
                new_id = cur.fetchone()[0]
        conn.close()
        return int(new_id)
    except Exception as exc:  # noqa: BLE001
        log.error("record_backup_run failed: %s", exc)
        return None
