"""Quarterly recovery rehearsal: restore latest backup into a throwaway
Postgres instance and run the audit verifier. Phase E4 deliverable.
"""
from __future__ import annotations

from pathlib import Path


def run_recovery_rehearsal(  # pragma: no cover — stub
    snapshot: Path,
) -> bool:
    """Restore + verify the snapshot. Returns True on a clean rehearsal. Phase E4.

    TODO(SP-7 Phase E4): pg_basebackup restore into a docker-compose ephemeral
    postgres, run alembic upgrade head, run verify_chain() across all chained
    tables, write a backup_runs row with type='recovery_rehearsal'.
    """
    raise NotImplementedError("run_recovery_rehearsal: Phase E4 deliverable")


if __name__ == "__main__":  # pragma: no cover — CLI lands in Phase E4
    raise NotImplementedError("CLI: Phase E4 deliverable")
