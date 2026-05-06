"""Rsync the latest snapshot to the operator's laptop SSD over SSH. Phase E3.

Env: ``LAPTOP_RSYNC_TARGET`` (e.g.,
``user@laptop.lan:/mnt/ext/trading-radar-backups/``).

If unset, returns ``False`` and logs a warning — the B2 upload is the primary
off-site copy, so missing the laptop sync is degraded but not fatal.

Tests mock ``subprocess.run``; no real rsync is invoked.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def rsync_to_laptop(snapshot_path: Path) -> bool:
    """Rsync ``snapshot_path`` to ``LAPTOP_RSYNC_TARGET``. Returns success/skip."""
    target = os.environ.get("LAPTOP_RSYNC_TARGET")
    if not target:
        log.warning(
            "LAPTOP_RSYNC_TARGET not set; skipping laptop rsync of %s",
            snapshot_path,
        )
        return False

    cmd = [
        "rsync", "-avz", "--partial", "--timeout=60",
        str(snapshot_path), target,
    ]
    log.info("rsync %s -> %s", snapshot_path, target)
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        log.error("rsync failed (rc=%d): %s", proc.returncode, stderr[:500])
        return False
    stdout = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    log.info("rsync ok: %s", stdout[:200])
    return True


if __name__ == "__main__":  # pragma: no cover — CLI exercised by E5 wiring
    import argparse
    import time
    from datetime import datetime, timezone

    from tools.backup._persistence import record_backup_run

    parser = argparse.ArgumentParser(description="rsync to laptop SSD")
    parser.add_argument("--snapshot-path", required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    snap = Path(args.snapshot_path)
    ok = rsync_to_laptop(snap)
    record_backup_run(
        backup_type="nightly_basebackup",
        target="laptop",
        success=ok,
        duration_seconds=time.monotonic() - started_mono,
        error_message=None if ok else "rsync skipped or failed",
        started_at=started_at,
    )
    raise SystemExit(0 if ok else 1)
