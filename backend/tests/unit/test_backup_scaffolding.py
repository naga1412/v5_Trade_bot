"""SP-7 Phase A5: tools/backup package stubs.

Same rationale as the app/ops scaffolding test (tests/unit/test_ops_scaffolding.py):
these stubs are import targets for Phase E TDD, not real implementations.
Each callable raises NotImplementedError with a Phase E pointer; the
SnapshotMetadata dataclass exposes its expected fields ahead of time so
the Phase E tests can construct fixtures against it.
"""
from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime
from pathlib import Path

import pytest


def test_tools_backup_package_imports() -> None:
    import tools.backup  # noqa: F401


def test_snapshot_metadata_dataclass_shape() -> None:
    from tools.backup.snapshot import SnapshotMetadata

    assert dataclasses.is_dataclass(SnapshotMetadata)
    fields = {f.name for f in dataclasses.fields(SnapshotMetadata)}
    assert fields == {"path", "size_bytes", "taken_at", "duration_seconds"}


def test_snapshot_metadata_is_frozen() -> None:
    from tools.backup.snapshot import SnapshotMetadata

    instance = SnapshotMetadata(
        path=Path("/tmp/snap"),
        size_bytes=1234,
        taken_at=datetime(2026, 5, 5),
        duration_seconds=1.5,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.size_bytes = 5678  # type: ignore[misc]


def test_snapshot_take_snapshot_is_callable() -> None:
    """Phase E1 replaced the stub with a real subprocess wrapper."""
    from tools.backup.snapshot import take_snapshot

    assert callable(take_snapshot)


def test_upload_b2_exposes_upload_to_b2_callable() -> None:
    from tools.backup.upload_b2 import upload_to_b2

    assert callable(upload_to_b2)
    sig = inspect.signature(upload_to_b2)
    # Spec §3.1: source path + destination prefix.
    assert "source" in sig.parameters or len(sig.parameters) >= 1


def test_upload_b2_is_callable() -> None:
    """Phase E2 replaced the stub with a real boto3 + AESGCM uploader."""
    from tools.backup.upload_b2 import upload_to_b2

    assert callable(upload_to_b2)


def test_rsync_laptop_exposes_rsync_to_laptop_callable() -> None:
    from tools.backup.rsync_laptop import rsync_to_laptop

    assert callable(rsync_to_laptop)


def test_rsync_laptop_is_a_stub() -> None:
    from tools.backup.rsync_laptop import rsync_to_laptop

    with pytest.raises(NotImplementedError, match="Phase E3"):
        rsync_to_laptop(Path("/tmp/snap.tar.zst"))


def test_recovery_rehearsal_exposes_run_recovery_rehearsal_callable() -> None:
    from tools.backup.recovery_rehearsal import run_recovery_rehearsal

    assert callable(run_recovery_rehearsal)


def test_recovery_rehearsal_is_a_stub() -> None:
    from tools.backup.recovery_rehearsal import run_recovery_rehearsal

    with pytest.raises(NotImplementedError, match="Phase E4"):
        run_recovery_rehearsal(Path("/tmp/snap.tar.zst"))
