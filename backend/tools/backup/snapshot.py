"""pg_basebackup wrapper. Phase E1 deliverable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SnapshotMetadata:
    path: Path
    size_bytes: int
    taken_at: datetime
    duration_seconds: float


def take_snapshot(out_dir: Path) -> SnapshotMetadata:  # pragma: no cover — stub
    """Run pg_basebackup into out_dir and return its metadata. Phase E1.

    TODO(SP-7 Phase E1): subprocess.run(['pg_basebackup', ...]), tar.zst
    the output, write a backup_runs row, return SnapshotMetadata.
    """
    raise NotImplementedError("take_snapshot: Phase E1 deliverable")


if __name__ == "__main__":  # pragma: no cover — CLI lands in Phase E1
    raise NotImplementedError("CLI: Phase E1 deliverable")
