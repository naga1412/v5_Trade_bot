"""pg_basebackup wrapper. Phase E1.

Invocation:
    take_snapshot(out_dir=Path("/var/backups/trading-radar/full_<ts>"))

Returns ``SnapshotMetadata(path, size_bytes, taken_at, duration_seconds)``.
Raises ``RuntimeError`` if ``pg_basebackup`` exits non-zero.

Env vars:
    BACKUP_PGHOST    (default "postgres" — docker-compose service name)
    BACKUP_PGPORT    (default "5432")
    BACKUP_PGUSER    (default "postgres")
    BACKUP_PGPASSWORD (default "" — passed via PGPASSWORD env to avoid pgpass file)

Note on test environment:
    ``pg_basebackup`` is NOT in the backend container's PATH (it ships inside
    the postgres image only). All unit tests therefore mock ``subprocess.run``
    and never exercise the real binary. In production, the script is invoked
    by cron on the Oracle host where the postgres-client package provides
    ``pg_basebackup``, OR via ``docker compose exec postgres pg_basebackup``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotMetadata:
    path: Path
    size_bytes: int
    taken_at: datetime
    duration_seconds: float


def take_snapshot(out_dir: Path) -> SnapshotMetadata:
    """Run ``pg_basebackup`` into ``out_dir`` and return metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    password = os.environ.get("BACKUP_PGPASSWORD", "")

    cmd = [
        "pg_basebackup",
        "-h", host, "-p", port, "-U", user,
        "-D", str(out_dir),
        "-Ft", "-z", "-X", "stream", "-P",
    ]
    env = {**os.environ, "PGPASSWORD": password}

    started = time.monotonic()
    taken_at = datetime.now(timezone.utc)
    log.info("pg_basebackup -> %s", out_dir)
    proc = subprocess.run(cmd, env=env, capture_output=True)
    duration = time.monotonic() - started

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        log.error("pg_basebackup failed: %s", stderr)
        raise RuntimeError(
            f"pg_basebackup failed (rc={proc.returncode}): {stderr[:500]}"
        )

    size = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    log.info("pg_basebackup ok: size=%d bytes duration=%.1fs", size, duration)
    return SnapshotMetadata(
        path=out_dir,
        size_bytes=size,
        taken_at=taken_at,
        duration_seconds=duration,
    )


if __name__ == "__main__":  # pragma: no cover — CLI exercised by E5 wiring
    import argparse

    from tools.backup._persistence import record_backup_run

    parser = argparse.ArgumentParser(description="pg_basebackup wrapper")
    parser.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    try:
        meta = take_snapshot(Path(args.out))
        record_backup_run(
            backup_type="nightly_basebackup",
            target="oracle_local",
            success=True,
            size_bytes=meta.size_bytes,
            duration_seconds=meta.duration_seconds,
            started_at=started_at,
        )
        print(f"path={meta.path} size={meta.size_bytes} duration={meta.duration_seconds:.1f}s")
    except Exception as exc:  # noqa: BLE001
        record_backup_run(
            backup_type="nightly_basebackup",
            target="oracle_local",
            success=False,
            error_message=str(exc)[:500],
            started_at=started_at,
        )
        raise
