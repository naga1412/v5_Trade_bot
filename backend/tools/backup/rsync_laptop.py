"""Rsync to operator's laptop over WireGuard. Phase E3 deliverable."""
from __future__ import annotations

from pathlib import Path


def rsync_to_laptop(  # pragma: no cover — stub
    source: Path,
) -> None:
    """Rsync the snapshot tarball to the operator's laptop. Phase E3.

    TODO(SP-7 Phase E3): subprocess.run(['rsync', '-avz', '--partial', ...])
    over the WG tunnel, write a backup_runs row on success/failure.
    """
    raise NotImplementedError("rsync_to_laptop: Phase E3 deliverable")


if __name__ == "__main__":  # pragma: no cover — CLI lands in Phase E3
    raise NotImplementedError("CLI: Phase E3 deliverable")
