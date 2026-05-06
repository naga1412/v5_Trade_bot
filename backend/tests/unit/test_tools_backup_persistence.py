"""Unit tests for tools/backup/_persistence.record_backup_run — Phase E5.

The helper is an `INSERT ... RETURNING id` against psycopg2. Tests mock the
``psycopg2.connect`` chain so the SQL shape and parameter ordering can be
verified without a real Postgres dependency.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tools.backup._persistence import record_backup_run


def _install_fake_psycopg2(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_id: int = 42,
    raise_on_connect: bool = False,
) -> dict:
    """Inject a fake ``psycopg2`` module into sys.modules. Returns capture dict."""
    captured: dict = {}

    fake_module = types.ModuleType("psycopg2")

    cursor = MagicMock()
    cursor.fetchone.return_value = (fake_id,)

    def execute_capture(sql, params):  # type: ignore[no-untyped-def]
        captured["sql"] = sql
        captured["params"] = params

    cursor.execute.side_effect = execute_capture

    cursor_cm = MagicMock()
    cursor_cm.__enter__ = lambda self: cursor
    cursor_cm.__exit__ = lambda *a: None

    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: None

    def fake_connect(**kwargs):  # type: ignore[no-untyped-def]
        if raise_on_connect:
            raise OSError("connection refused")
        captured["connect_kwargs"] = kwargs
        return conn

    fake_module.connect = fake_connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", fake_module)
    return captured


def test_record_backup_run_emits_insert_returning_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper INSERTs and returns the new row id."""
    monkeypatch.setenv("BACKUP_PGHOST", "test-host")
    monkeypatch.setenv("BACKUP_PGPORT", "5432")
    monkeypatch.setenv("BACKUP_PGUSER", "tester")
    monkeypatch.setenv("BACKUP_PGPASSWORD", "pw")
    monkeypatch.setenv("POSTGRES_DB", "test_db")
    captured = _install_fake_psycopg2(monkeypatch, fake_id=99)

    started = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    new_id = record_backup_run(
        backup_type="nightly_basebackup",
        target="oracle_local",
        success=True,
        size_bytes=2048,
        duration_seconds=12.5,
        started_at=started,
    )

    assert new_id == 99
    assert "INSERT INTO backup_runs" in captured["sql"]
    assert "RETURNING id" in captured["sql"]
    params = captured["params"]
    # (started_at, completed_at, backup_type, target, success, size, dur, err_msg)
    assert params[0] == started
    assert params[2] == "nightly_basebackup"
    assert params[3] == "oracle_local"
    assert params[4] is True
    assert params[5] == 2048
    assert params[6] == 12.5
    assert params[7] is None
    assert captured["connect_kwargs"]["host"] == "test-host"
    assert captured["connect_kwargs"]["dbname"] == "test_db"


def test_record_backup_run_returns_none_when_psycopg2_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No psycopg2 -> log + return None (do NOT raise)."""
    # Force ImportError by setting psycopg2 to None in sys.modules
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    new_id = record_backup_run(
        backup_type="hourly_dump",
        target="oracle_local",
        success=True,
    )
    assert new_id is None


def test_record_backup_run_returns_none_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect failure is swallowed — backup metadata is best-effort."""
    _install_fake_psycopg2(monkeypatch, raise_on_connect=True)
    new_id = record_backup_run(
        backup_type="recovery_rehearsal",
        target="rehearsal_restore",
        success=False,
        error_message="something went wrong",
    )
    assert new_id is None


def test_record_backup_run_persists_failure_with_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure rows include the error message and success=False."""
    captured = _install_fake_psycopg2(monkeypatch, fake_id=7)
    new_id = record_backup_run(
        backup_type="nightly_basebackup",
        target="b2",
        success=False,
        duration_seconds=3.2,
        error_message="upload timeout after 60s",
    )
    assert new_id == 7
    params = captured["params"]
    assert params[2] == "nightly_basebackup"
    assert params[3] == "b2"
    assert params[4] is False
    assert params[6] == 3.2
    assert params[7] == "upload timeout after 60s"
