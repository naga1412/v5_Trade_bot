"""Quarterly recovery rehearsal — pull latest B2 backup, restore, verify counts.

Phase E4. Steps:
    1. List latest object in ``s3://${B2_BUCKET}/db-snapshots/``
    2. Download + decrypt + extract into a throwaway directory
    3. Restore into a throwaway database (``recovery_test``) on the same Postgres
    4. ``SELECT COUNT(*)`` from each chained table in BOTH prod and recovery
    5. Compare; success iff ``|delta| <= COUNT_TOLERANCE`` for every table
    6. Drop ``recovery_test`` (always, even on failure)
    7. Return :class:`RecoveryReport`

Triggered manually OR by a quarterly cron (``0 12 1 */3 *``).

The actual ``pg_restore`` step is documented as a v1 manual step (the current
implementation logs the intent but does not exec the restore — pg_basebackup
output is a tar of the data directory, not a logical dump suitable for
``pg_restore`` into a new DB on the same instance). v2 will switch to
``pg_dump --format=custom`` for fully-automated restore. Tests mock the
restore + row-count steps; the real value of this module today is the
orchestration sequence and the row-count comparison logic.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from tools.backup.upload_b2 import _load_key, decrypt_bytes_aes_gcm

log = logging.getLogger(__name__)

CHAINED_TABLES: tuple[str, ...] = ("predictions", "paper_trades", "shadow_trades")
RECOVERY_DB_NAME = "recovery_test"
COUNT_TOLERANCE = 1


@dataclass
class RecoveryReport:
    success: bool
    started_at: datetime
    completed_at: datetime
    prod_counts: dict[str, int] = field(default_factory=dict)
    recovery_counts: dict[str, int] = field(default_factory=dict)
    deltas: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _list_latest_b2_backup(s3: Any, bucket: str) -> str:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="db-snapshots/")
    contents = resp.get("Contents", [])
    if not contents:
        raise RuntimeError("no backups found in B2")
    latest = max(contents, key=lambda x: x["LastModified"])
    return str(latest["Key"])


def _download_and_decrypt(
    s3: Any, bucket: str, key: str, dest_dir: Path,
) -> Path:
    enc_path = dest_dir / "downloaded.tar.gz.enc"
    s3.download_file(bucket, key, str(enc_path))
    blob = enc_path.read_bytes()
    plain = decrypt_bytes_aes_gcm(blob, key=_load_key())
    plain_path = dest_dir / "decrypted.tar.gz"
    plain_path.write_bytes(plain)
    return plain_path


def _extract_tarball(tarball: Path, dest_dir: Path) -> None:
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(dest_dir)  # noqa: S202 — trusted internal backup


def _restore_to_throwaway_db(extracted_dir: Path) -> None:
    """CREATE DATABASE recovery_test + (would) pg_restore into it.

    v1 limitation: pg_basebackup output is a tar of the data directory, not a
    logical dump. Restoring to a side DB on the SAME instance requires either
    (a) spinning up a separate postgres process that points at the extracted
    data dir, or (b) switching the backup format to pg_dump --format=custom.
    For now we only DROP+CREATE the recovery DB; the real restore is a
    documented manual step. Tests mock this function so the orchestration is
    still verified.
    """
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    env = {**os.environ, "PGPASSWORD": pw}
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {RECOVERY_DB_NAME};"],
        env=env, check=True, capture_output=True,
    )
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"CREATE DATABASE {RECOVERY_DB_NAME};"],
        env=env, check=True, capture_output=True,
    )
    log.info(
        "recovery restore: created %s (manual pg_restore step from %s)",
        RECOVERY_DB_NAME, extracted_dir,
    )


def _row_count(*, table: str, which: str) -> int:
    """Query ``SELECT COUNT(*) FROM {table}`` in either prod or recovery DB."""
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    if which == "prod":
        db = os.environ.get("POSTGRES_DB", "trading_radar")
    else:
        db = RECOVERY_DB_NAME
    env = {**os.environ, "PGPASSWORD": pw}
    proc = subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", db,
         "-tAc", f"SELECT COUNT(*) FROM {table};"],
        env=env, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return -1
    out = proc.stdout.decode("utf-8").strip()
    return int(out) if out else 0


def _drop_recovery_db() -> None:
    host = os.environ.get("BACKUP_PGHOST", "postgres")
    port = os.environ.get("BACKUP_PGPORT", "5432")
    user = os.environ.get("BACKUP_PGUSER", "postgres")
    pw = os.environ.get("BACKUP_PGPASSWORD", "")
    env = {**os.environ, "PGPASSWORD": pw}
    subprocess.run(
        ["psql", "-h", host, "-p", port, "-U", user, "-d", "postgres",
         "-c", f"DROP DATABASE IF EXISTS {RECOVERY_DB_NAME};"],
        env=env, check=False, capture_output=True,
    )


def run_recovery_rehearsal(*, work_dir: Path) -> RecoveryReport:
    started = datetime.now(timezone.utc)
    bucket = os.environ.get("B2_BUCKET")
    if not bucket:
        return RecoveryReport(
            success=False, started_at=started,
            completed_at=datetime.now(timezone.utc),
            error="B2_BUCKET not set",
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get(
            "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
        ),
    )
    try:
        latest_key = _list_latest_b2_backup(s3, bucket)
        log.info("rehearsal: latest backup=%s", latest_key)
        decrypted = _download_and_decrypt(s3, bucket, latest_key, work_dir)
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(exist_ok=True)
        _extract_tarball(decrypted, extract_dir)
        _restore_to_throwaway_db(extract_dir)

        prod_counts = {
            t: _row_count(table=t, which="prod") for t in CHAINED_TABLES
        }
        rec_counts = {
            t: _row_count(table=t, which="recovery") for t in CHAINED_TABLES
        }
        deltas = {t: rec_counts[t] - prod_counts[t] for t in CHAINED_TABLES}
        success = all(abs(d) <= COUNT_TOLERANCE for d in deltas.values())

        return RecoveryReport(
            success=success,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            prod_counts=prod_counts,
            recovery_counts=rec_counts,
            deltas=deltas,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("recovery rehearsal failed")
        return RecoveryReport(
            success=False,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            error=str(exc)[:500],
        )
    finally:
        try:
            _drop_recovery_db()
        except Exception:  # noqa: BLE001
            log.exception("could not drop recovery db")


if __name__ == "__main__":  # pragma: no cover — CLI exercised by E5 wiring
    import argparse
    import time

    from tools.backup._persistence import record_backup_run

    parser = argparse.ArgumentParser(description="quarterly recovery rehearsal")
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    report = run_recovery_rehearsal(work_dir=Path(args.work_dir))
    record_backup_run(
        backup_type="recovery_rehearsal",
        target="rehearsal_restore",
        success=report.success,
        duration_seconds=time.monotonic() - started_mono,
        error_message=report.error,
        started_at=started_at,
    )
    print(f"success={report.success}")
    print(f"prod_counts={report.prod_counts}")
    print(f"recovery_counts={report.recovery_counts}")
    print(f"deltas={report.deltas}")
    raise SystemExit(0 if report.success else 1)
