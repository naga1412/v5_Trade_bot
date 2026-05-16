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


import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.db.audit import insert_with_chain


@pytest.mark.asyncio
async def test_insert_with_chain_links_rows_correctly() -> None:
    # Use a whitelisted table (predictions) with minimal required schema.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        h1 = await insert_with_chain(session, "predictions", {"symbol": "BTCUSDT"})
        h2 = await insert_with_chain(session, "predictions", {"symbol": "ETHUSDT"})
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT id, symbol, prev_hash, row_hash FROM predictions ORDER BY id")
        )).all()
    assert len(rows) == 2
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[0].row_hash == h1
    assert rows[1].prev_hash == h1
    assert rows[1].row_hash == h2
