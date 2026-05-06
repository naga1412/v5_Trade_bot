"""Unit tests for tools/backup/upload_b2.py — Phase E2."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.backup.upload_b2 import (
    decrypt_bytes_aes_gcm,
    encrypt_file_aes_gcm,
    upload_to_b2,
)


def test_encrypt_decrypt_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"hello world payload")
    key = os.urandom(32)
    ct_path = encrypt_file_aes_gcm(src, key=key, out_path=tmp_path / "in.enc")
    assert ct_path.exists()
    pt = decrypt_bytes_aes_gcm(ct_path.read_bytes(), key=key)
    assert pt == b"hello world payload"


def test_decrypt_with_wrong_key_raises(tmp_path: Path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 100)
    key = os.urandom(32)
    ct_path = encrypt_file_aes_gcm(src, key=key, out_path=tmp_path / "in.enc")
    with pytest.raises(Exception):  # noqa: B017,PT011 — cryptography.InvalidTag
        decrypt_bytes_aes_gcm(ct_path.read_bytes(), key=os.urandom(32))


def test_upload_to_b2_invokes_boto3_with_correct_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "B2_S3_ENDPOINT", "https://s3.us-west-002.backblazeb2.com",
    )
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )

    snapshot_dir = tmp_path / "full_2026"
    snapshot_dir.mkdir()
    (snapshot_dir / "base.tar.gz").write_bytes(b"compressed payload")

    fake_s3 = MagicMock()
    with patch("boto3.client", return_value=fake_s3):
        result_uri = upload_to_b2(snapshot_dir)

    assert fake_s3.upload_file.called
    args, _kwargs = fake_s3.upload_file.call_args
    # args[0] = local path; args[1] = bucket; args[2] = key
    assert args[1] == "test-bucket"
    assert args[2].startswith("db-snapshots/")
    assert result_uri.startswith("s3://test-bucket/db-snapshots/")
    assert result_uri.endswith(".tar.gz.enc")


def test_upload_to_b2_raises_when_bucket_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("B2_BUCKET", raising=False)
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode("ascii"),
    )
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "f.tar.gz").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="B2_BUCKET"):
        upload_to_b2(snap)


def test_upload_to_b2_raises_when_encryption_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("B2_BUCKET", "test-bucket")
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "f.tar.gz").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="BACKUP_ENCRYPTION_KEY"):
        upload_to_b2(snap)
