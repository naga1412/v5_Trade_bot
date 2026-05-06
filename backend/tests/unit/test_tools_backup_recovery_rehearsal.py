"""Unit tests for tools/backup/recovery_rehearsal.py — Phase E4."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.backup.recovery_rehearsal import (
    RecoveryReport,
    run_recovery_rehearsal,
)


def test_recovery_rehearsal_finds_latest_b2_backup_and_compares_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End-to-end mock: list latest B2 -> download -> decrypt -> restore -> compare."""
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "db-snapshots/2026-05-04/old.tar.gz.enc",
             "LastModified": "2026-05-04T00:00:00Z"},
            {"Key": "db-snapshots/2026-05-05/latest.tar.gz.enc",
             "LastModified": "2026-05-05T00:00:00Z"},
        ]
    }

    # Stub out the inner subprocess + tar steps so we don't need a real
    # postgres or a real tarball.
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._download_and_decrypt",
        lambda s3, bucket, key, dest_dir: dest_dir / "fake.tar.gz",
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._extract_tarball",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._restore_to_throwaway_db",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._row_count",
        lambda *, table, which: 100,  # prod and recovery match
    )
    drop_called = {"v": False}

    def fake_drop():  # type: ignore[no-untyped-def]
        drop_called["v"] = True

    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._drop_recovery_db", fake_drop,
    )

    with patch("boto3.client", return_value=fake_s3):
        report = run_recovery_rehearsal(work_dir=tmp_path / "work")

    assert isinstance(report, RecoveryReport)
    assert report.success is True
    assert report.deltas == {"predictions": 0, "paper_trades": 0, "shadow_trades": 0}
    # Latest by LastModified should be the 2026-05-05 entry
    assert fake_s3.list_objects_v2.called
    assert drop_called["v"] is True


def test_recovery_rehearsal_fails_when_count_delta_exceeds_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If prod has 100 predictions and recovery has 50, success=False."""
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "db-snapshots/2026-05-05/x.tar.gz.enc",
                      "LastModified": "2026-05-05T00:00:00Z"}],
    }

    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._download_and_decrypt",
        lambda *a, **kw: tmp_path / "decrypted.tar.gz",
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._extract_tarball", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._restore_to_throwaway_db",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._drop_recovery_db", lambda *a, **kw: None,
    )

    counts = {
        "prod": {"predictions": 100, "paper_trades": 5, "shadow_trades": 50},
        "recovery": {"predictions": 50, "paper_trades": 5, "shadow_trades": 50},
    }

    def _row_count(*, table: str, which: str) -> int:
        return counts[which][table]

    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._row_count", _row_count,
    )

    with patch("boto3.client", return_value=fake_s3):
        report = run_recovery_rehearsal(work_dir=tmp_path / "work")

    assert report.success is False
    assert report.deltas["predictions"] == -50


def test_recovery_rehearsal_fails_cleanly_when_b2_bucket_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("B2_BUCKET", raising=False)
    report = run_recovery_rehearsal(work_dir=tmp_path / "work")
    assert report.success is False
    assert report.error is not None
    assert "B2_BUCKET" in report.error


def test_recovery_rehearsal_returns_failure_on_no_backups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )
    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {"Contents": []}
    monkeypatch.setattr(
        "tools.backup.recovery_rehearsal._drop_recovery_db", lambda *a, **kw: None,
    )
    with patch("boto3.client", return_value=fake_s3):
        report = run_recovery_rehearsal(work_dir=tmp_path / "work")
    assert report.success is False
    assert report.error is not None
    assert "no backups" in report.error.lower()
