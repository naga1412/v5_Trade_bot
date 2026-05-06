"""Encrypt + upload backup snapshots to Backblaze B2 (S3-compatible). Phase E2.

Encryption: AES-256-GCM. Key is base64(32 bytes) from env BACKUP_ENCRYPTION_KEY.
On-disk ciphertext format: ``nonce(12) || ciphertext || GCM tag(16)``. This
matches the convention used by ``app/auth/secrets.py`` (per-user secrets), but
this module derives the key from a system-wide passphrase via env var rather
than per-user salt — backup ciphertext is the same shape as user-secret
ciphertext, just with a different key-management strategy.

Upload: boto3 against the B2 S3-compatible endpoint. Bucket from env
``B2_BUCKET``; endpoint from env ``B2_S3_ENDPOINT`` (defaults to us-west-002).

Tests use ``unittest.mock.patch("boto3.client")`` returning a stub client with
``.upload_file(local_path, bucket, key)`` capture — no real S3 calls.
"""
from __future__ import annotations

import base64
import logging
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_NONCE_SIZE = 12


def _load_key() -> bytes:
    """Decode and validate ``BACKUP_ENCRYPTION_KEY`` (32 bytes base64)."""
    raw = os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY env var not set (32-byte base64 expected)"
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"BACKUP_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}"
        )
    return key


def encrypt_file_aes_gcm(src: Path, *, key: bytes, out_path: Path) -> Path:
    """AES-256-GCM encrypt ``src`` -> ``out_path``. Returns ``out_path``.

    Output format: nonce(12) || ciphertext || gcm_tag(16) — single blob.
    """
    aes = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    pt = src.read_bytes()
    ct = aes.encrypt(nonce, pt, associated_data=None)
    out_path.write_bytes(nonce + ct)
    return out_path


def decrypt_bytes_aes_gcm(blob: bytes, *, key: bytes) -> bytes:
    """Inverse of :func:`encrypt_file_aes_gcm` (operates on bytes)."""
    aes = AESGCM(key)
    nonce, ct = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return aes.decrypt(nonce, ct, associated_data=None)


def upload_to_b2(snapshot_dir: Path) -> str:
    """Tar (if needed), encrypt, upload to B2. Returns the s3:// URI."""
    bucket = os.environ.get("B2_BUCKET")
    if not bucket:
        raise RuntimeError("B2_BUCKET env var not set")
    endpoint = os.environ.get(
        "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
    )
    key = _load_key()

    # Step 1: tar the snapshot directory if it isn't already a single tarball
    contents = list(snapshot_dir.iterdir())
    cleanup_tarball = False
    if (
        len(contents) == 1
        and (
            contents[0].name.endswith(".tar.gz")
            or contents[0].suffix in (".tgz", ".tar")
        )
    ):
        tarball = contents[0]
    else:
        tarball = snapshot_dir.parent / f"{snapshot_dir.name}.tar.gz"
        with tarfile.open(tarball, "w:gz") as tf:
            tf.add(snapshot_dir, arcname=snapshot_dir.name)
        cleanup_tarball = True

    # Step 2: encrypt
    encrypted = snapshot_dir.parent / f"{snapshot_dir.name}.tar.gz.enc"
    encrypt_file_aes_gcm(tarball, key=key, out_path=encrypted)

    # Step 3: upload
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s3_key = f"db-snapshots/{today}/{encrypted.name}"
    s3 = boto3.client("s3", endpoint_url=endpoint)
    log.info("uploading %s -> s3://%s/%s", encrypted, bucket, s3_key)
    s3.upload_file(str(encrypted), bucket, s3_key)

    if cleanup_tarball:
        tarball.unlink(missing_ok=True)

    return f"s3://{bucket}/{s3_key}"


if __name__ == "__main__":  # pragma: no cover — CLI exercised by E5 wiring
    import argparse
    import time

    from tools.backup._persistence import record_backup_run

    parser = argparse.ArgumentParser(description="encrypt + upload to B2")
    parser.add_argument("--snapshot-dir", required=True)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    snap = Path(args.snapshot_dir)
    try:
        uri = upload_to_b2(snap)
        size = sum(p.stat().st_size for p in snap.rglob("*") if p.is_file())
        record_backup_run(
            backup_type="nightly_basebackup",
            target="b2",
            success=True,
            size_bytes=size,
            duration_seconds=time.monotonic() - started_mono,
            started_at=started_at,
        )
        print(f"uploaded: {uri}")
    except Exception as exc:  # noqa: BLE001
        record_backup_run(
            backup_type="nightly_basebackup",
            target="b2",
            success=False,
            duration_seconds=time.monotonic() - started_mono,
            error_message=str(exc)[:500],
            started_at=started_at,
        )
        raise
