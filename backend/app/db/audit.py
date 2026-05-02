import hashlib
import json
from typing import Any

GENESIS_HASH: str = "0" * 64


def canonical_row_json(row: dict[str, Any]) -> str:
    """Canonical JSON serialization for hashing.

    sort_keys=True and the compact separators give a deterministic
    byte representation, so the same row always hashes to the same value.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    payload = (prev_hash + canonical_row_json(row)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
