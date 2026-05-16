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
        # Subset of HASH_PAYLOAD_COLUMNS["predictions"] — the 8 ghost_* /
        # model_checkpoint_id columns are intentionally absent. Callers
        # only include them when an active ML checkpoint is loaded;
        # _filter_for_hash drops missing-from-payload keys naturally,
        # so this fixture exercises the chain on a minimal-row case.
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


@pytest.fixture
async def chain_session_with_pr1_cols():
    """Same as chain_session but includes the 7 PR1 analytics columns.

    Simulates the post-migration schema where the new columns exist but
    are NULL for rows inserted before Phase 5 started writing them.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, timeframe TEXT, "
            "ts TEXT, layer_scores TEXT, final_score REAL, "
            "direction TEXT, confidence REAL, inputs_hash TEXT, "
            "model_version TEXT, cold_start INTEGER, "
            # PR1 Phase 5 columns (NULL by default)
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, p_win REAL, "
            "effective_score REAL, realized_vol_20d REAL, "
            "funding_directional_adj REAL, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_existing_rows_replay_with_new_columns_added(chain_session_with_pr1_cols):
    """Insert a row with the pre-migration shape (7 new cols NULL), re-compute
    row_hash via the whitelist — must match the stored hash.

    Rationale: the 7 PR1 columns are in NON_HASHED_ALLOW_LIST, NOT in
    HASH_PAYLOAD_COLUMNS. _filter_for_hash only keeps keys in
    HASH_PAYLOAD_COLUMNS; therefore rows inserted before the migration
    have their hash recomputed identically after the migration adds the
    new columns (which are NULL and thus dropped by the filter).
    """
    s = chain_session_with_pr1_cols
    row = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-17T00:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.65, "inputs_hash": "h_pr1",
        "model_version": "sp-0", "cold_start": 0,
        # PR1 Phase 5 columns — NULL (not yet populated; simulates pre-hook rows)
        "mtf_agreement": None, "mtf_dominant_tf": None,
        "mtf_directions_json": None, "p_win": None,
        "effective_score": None, "realized_vol_20d": None,
        "funding_directional_adj": None,
    }
    stored_hash = await insert_with_chain(s, "predictions", row)

    db_rows = (await s.execute(sa.text(
        "SELECT * FROM predictions ORDER BY id"
    ))).all()
    assert len(db_rows) == 1
    row_dict = dict(db_rows[0]._mapping)
    row_dict.pop("id", None)
    row_dict.pop("prev_hash", None)
    row_dict.pop("row_hash", None)

    # The new columns are present in the dict from the DB read but must be
    # excluded from the hash because they're in NON_HASHED_ALLOW_LIST.
    filtered = _filter_for_hash("predictions", row_dict)

    # Confirm new columns were dropped by the filter
    for col in (
        "mtf_agreement", "mtf_dominant_tf", "mtf_directions_json",
        "p_win", "effective_score", "realized_vol_20d", "funding_directional_adj",
    ):
        assert col not in filtered, (
            f"PR1 column '{col}' must NOT be in the hash filter output"
        )

    from app.db.audit import GENESIS_HASH
    recomputed = compute_row_hash(GENESIS_HASH, filtered)
    assert recomputed == stored_hash, (
        f"Replay mismatch: stored={stored_hash} recomputed={recomputed}"
    )


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
