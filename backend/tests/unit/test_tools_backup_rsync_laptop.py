"""Unit tests for tools/backup/rsync_laptop.py — Phase E3."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from tools.backup.rsync_laptop import rsync_to_laptop


def test_rsync_to_laptop_invokes_rsync_with_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LAPTOP_RSYNC_TARGET",
        "user@laptop.lan:/mnt/ext/trading-radar-backups/",
    )
    captured: dict = {}

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"", stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is True
    assert "rsync" in captured["cmd"][0]
    joined = " ".join(captured["cmd"])
    assert str(src) in joined
    assert "user@laptop.lan:/mnt/ext/trading-radar-backups/" in joined
    assert "-avz" in captured["cmd"]
    assert "--partial" in captured["cmd"]


def test_rsync_skipped_when_target_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("LAPTOP_RSYNC_TARGET", raising=False)
    caplog.set_level(logging.WARNING, logger="tools.backup.rsync_laptop")
    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is False
    assert any("LAPTOP_RSYNC_TARGET" in r.message for r in caplog.records)


def test_rsync_returns_false_on_subprocess_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAPTOP_RSYNC_TARGET", "x@y:/z")

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=cmd, returncode=23, stdout=b"", stderr=b"err",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    src = tmp_path / "snap"
    src.mkdir()
    ok = rsync_to_laptop(src)
    assert ok is False
