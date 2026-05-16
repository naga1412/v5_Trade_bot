"""Replay-identity: re-hash known fixture rows, assert match.

For the prod-data replay step, see manual verification in
docs/superpowers/plans/2026-05-16-pr1-record-only.md Task 7.2.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import (
    GENESIS_HASH, compute_row_hash, _filter_for_hash, insert_with_chain,
)


@pytest.fixture
async def chain_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table, cols in [
            ("predictions", "user_id INTEGER, symbol TEXT, timeframe TEXT, "
                           "ts TEXT, layer_scores TEXT, final_score REAL, "
                           "direction TEXT, confidence REAL, inputs_hash TEXT, "
                           "model_version TEXT, cold_start INTEGER"),
        ]:
            await conn.execute(sa.text(
                f"CREATE TABLE {table} ("
                f"id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, "
                f"prev_hash TEXT, row_hash TEXT)"
            ))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_replay_3_row_chain_identity(chain_session):
    """Build a 3-row chain via insert_with_chain, re-compute each
    expected_hash by hand using _filter_for_hash, assert match."""
    s = chain_session
    rows = [
        {"user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
         "ts": f"2026-05-16T1{i}:00:00+00:00",
         "layer_scores": "{}", "final_score": 0.30 + i * 0.05,
         "direction": "LONG", "confidence": 0.6, "inputs_hash": f"h{i}",
         "model_version": "sp-0", "cold_start": 0}
        for i in range(3)
    ]
    stored_hashes = []
    for row in rows:
        stored_hashes.append(await insert_with_chain(s, "predictions", row))

    # Re-read all rows + recompute
    db_rows = (await s.execute(sa.text(
        "SELECT * FROM predictions ORDER BY id"
    ))).all()
    prev = GENESIS_HASH
    for db_row, expected_stored in zip(db_rows, stored_hashes, strict=True):
        row_dict = dict(db_row._mapping)
        # Drop id + chain meta from the dict before filter, mimicking what
        # the prod verifier reads (it doesn't know about id).
        row_dict.pop("id", None)
        row_dict.pop("prev_hash", None)
        row_dict.pop("row_hash", None)
        filtered = _filter_for_hash("predictions", row_dict)
        recomputed = compute_row_hash(prev, filtered)
        assert recomputed == expected_stored, (
            f"replay mismatch on row id={db_row.id}: "
            f"stored={expected_stored} recomputed={recomputed}"
        )
        prev = expected_stored
