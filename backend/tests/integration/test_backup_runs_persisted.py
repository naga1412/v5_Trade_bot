"""Integration test: tools/backup/_persistence.record_backup_run writes a row.

Phase E5. Skips when psycopg2 isn't installed or Postgres isn't reachable
(local Windows dev runs sqlite + lacks psycopg2). In CI / docker, the test
runs against the real Postgres test DB and asserts the inserted row appears
in ``backup_runs``.
"""
from __future__ import annotations

import os

import pytest


def test_record_backup_run_persists_row_to_postgres() -> None:
    """End-to-end: helper INSERTs and the row is queryable."""
    psycopg2 = pytest.importorskip("psycopg2")

    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = int(os.environ.get("BACKUP_PGPORT", "5432"))
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "trading_radar")

    # Probe connection — skip if Postgres isn't reachable in this env.
    try:
        probe = psycopg2.connect(
            host=host, port=port, user=user, password=pw, dbname=db,
        )
        probe.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres not reachable for integration test: {exc}")

    from tools.backup._persistence import record_backup_run

    new_id = record_backup_run(
        backup_type="nightly_basebackup",
        target="oracle_local",
        success=True,
        size_bytes=1024,
        duration_seconds=2.5,
    )
    assert new_id is not None and new_id > 0

    conn = psycopg2.connect(
        host=host, port=port, user=user, password=pw, dbname=db,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT backup_type, target, success, size_bytes, "
                "duration_seconds FROM backup_runs WHERE id = %s",
                (new_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "nightly_basebackup"
    assert row[1] == "oracle_local"
    assert row[2] is True
    assert row[3] == 1024
    assert row[4] == pytest.approx(2.5)
