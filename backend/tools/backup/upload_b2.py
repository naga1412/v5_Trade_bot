"""Backblaze B2 uploader for snapshot tarballs. Phase E2 deliverable."""
from __future__ import annotations

from pathlib import Path


def upload_to_b2(  # pragma: no cover — stub
    source: Path,
    *,
    dest_prefix: str,
) -> str:
    """Upload `source` to b2://<bucket>/<dest_prefix>/<basename>. Phase E2.

    Returns the full b2:// URI on success.

    TODO(SP-7 Phase E2): boto3 S3-compatible client against B2's
    s3-eu-central-003 endpoint, multipart upload for >100MB, write a
    backup_runs row.
    """
    raise NotImplementedError("upload_to_b2: Phase E2 deliverable")


if __name__ == "__main__":  # pragma: no cover — CLI lands in Phase E2
    raise NotImplementedError("CLI: Phase E2 deliverable")
