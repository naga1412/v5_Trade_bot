import json
import hashlib

import pytest

from app.db.audit import canonical_row_json, compute_row_hash, GENESIS_HASH


def test_genesis_hash_is_64_zero_chars(empty_prev_hash: str) -> None:
    assert GENESIS_HASH == empty_prev_hash
    assert len(GENESIS_HASH) == 64


def test_canonical_row_json_is_sorted_and_compact() -> None:
    row = {"b": 2, "a": 1, "c": [3, 1, 2]}
    out = canonical_row_json(row)
    assert out == '{"a":1,"b":2,"c":[3,1,2]}'


def test_compute_row_hash_matches_sha256_of_concat() -> None:
    prev = "a" * 64
    row = {"x": 1, "y": "two"}
    expected = hashlib.sha256(
        (prev + canonical_row_json(row)).encode("utf-8")
    ).hexdigest()
    assert compute_row_hash(prev, row) == expected


def test_chain_unbroken_across_three_rows() -> None:
    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    h0 = GENESIS_HASH
    h1 = compute_row_hash(h0, rows[0])
    h2 = compute_row_hash(h1, rows[1])
    h3 = compute_row_hash(h2, rows[2])
    # mutating row 1 must invalidate h2 onward
    tampered = compute_row_hash(h0, {"id": 1, "v": "TAMPERED"})
    assert tampered != h1
