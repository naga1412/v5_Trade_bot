"""Unit tests for tools/backup/snapshot.py — Phase E1.

Notes:
    The host pytest environment may not have ``pg_basebackup`` on PATH (the
    binary lives inside the postgres container, not the backend container).
    Every test mocks ``subprocess.run`` so no real binary is invoked.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.backup.snapshot import SnapshotMetadata, take_snapshot


def test_take_snapshot_invokes_pg_basebackup_with_correct_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pg_basebackup is called with -D <out_dir> -Ft -z -X stream -P + PGPASSWORD env."""
    monkeypatch.setenv("BACKUP_PGHOST", "postgres")
    monkeypatch.setenv("BACKUP_PGPORT", "5432")
    monkeypatch.setenv("BACKUP_PGUSER", "postgres")
    monkeypatch.setenv("BACKUP_PGPASSWORD", "secret")

    captured: dict = {}

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        # Simulate basebackup output by writing a test file
        (tmp_path / "base.tar.gz").write_bytes(b"x" * 1024)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    meta = take_snapshot(tmp_path)
    assert isinstance(meta, SnapshotMetadata)
    assert meta.path == tmp_path
    assert meta.size_bytes >= 1024
    assert meta.duration_seconds >= 0.0
    assert "pg_basebackup" in captured["cmd"][0]
    assert "-D" in captured["cmd"]
    assert str(tmp_path) in captured["cmd"]
    assert "-Ft" in captured["cmd"]
    assert "-z" in captured["cmd"]
    assert "-X" in captured["cmd"]
    assert "stream" in captured["cmd"]
    assert captured["env"]["PGPASSWORD"] == "secret"


def test_take_snapshot_raises_on_pg_basebackup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_PGHOST", "postgres")
    monkeypatch.setenv("BACKUP_PGUSER", "postgres")
    monkeypatch.setenv("BACKUP_PGPASSWORD", "x")

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout=b"", stderr=b"connection refused",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="pg_basebackup failed"):
        take_snapshot(tmp_path)


def test_take_snapshot_uses_env_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defaults: host=postgres, port=5432, user=postgres when env unset."""
    monkeypatch.delenv("BACKUP_PGHOST", raising=False)
    monkeypatch.delenv("BACKUP_PGPORT", raising=False)
    monkeypatch.delenv("BACKUP_PGUSER", raising=False)
    monkeypatch.delenv("BACKUP_PGPASSWORD", raising=False)

    captured: dict = {}

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    take_snapshot(tmp_path)
    assert "postgres" in captured["cmd"]
    assert "5432" in captured["cmd"]
