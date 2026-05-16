"""Whitelist-aware insert_with_chain — keys outside the whitelist must not affect row_hash."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.audit import (
    HASH_PAYLOAD_COLUMNS,
    compute_row_hash,
    insert_with_chain,
)


@pytest.fixture
async def sqlite_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, symbol TEXT, timeframe TEXT,
                ts TEXT, layer_scores TEXT, final_score REAL,
                direction TEXT, confidence REAL, inputs_hash TEXT,
                model_version TEXT, cold_start INTEGER,
                extra_recording_only_col REAL,
                prev_hash TEXT, row_hash TEXT
            )
        """))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_whitelist_excludes_recording_only_column_from_hash(sqlite_session):
    """A column not in HASH_PAYLOAD_COLUMNS['predictions'] must not alter row_hash."""
    base_payload = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
    }
    # Insert row 1 with no extra column
    hash_without = await insert_with_chain(
        sqlite_session, "predictions", base_payload,
    )

    # Reset for a fresh chain
    await sqlite_session.execute(sa.text("DELETE FROM predictions"))
    # Insert row 1 again with an extra non-whitelisted column
    payload_with_extra = {**base_payload, "extra_recording_only_col": 42.0}
    hash_with = await insert_with_chain(
        sqlite_session, "predictions", payload_with_extra,
    )
    assert hash_without == hash_with, (
        "row_hash must be identical when only a non-whitelisted column differs"
    )


async def test_whitelist_includes_whitelisted_column_in_hash(sqlite_session):
    """A column IN the whitelist MUST contribute to row_hash."""
    base = {
        "user_id": 1, "symbol": "BTCUSDT", "timeframe": "1h",
        "ts": "2026-05-16T10:00:00+00:00",
        "layer_scores": "{}", "final_score": 0.35,
        "direction": "LONG", "confidence": 0.6,
        "inputs_hash": "abc", "model_version": "sp-0",
        "cold_start": 0,
    }
    hash_a = await insert_with_chain(sqlite_session, "predictions", base)
    await sqlite_session.execute(sa.text("DELETE FROM predictions"))
    different = {**base, "final_score": 0.36}  # whitelisted column, different value
    hash_b = await insert_with_chain(sqlite_session, "predictions", different)
    assert hash_a != hash_b, (
        "row_hash must differ when a whitelisted column value differs"
    )


def test_hash_payload_columns_covers_expected_tables():
    # Core chained tables from plan (predictions, shadow_trades, live_trades,
    # paper_trades) plus the 3 additional hash-chained tables discovered at
    # call-site audit (brain_decisions, tax_events, mode_change_log).
    expected = {
        "predictions", "shadow_trades", "live_trades", "paper_trades",
        "brain_decisions", "tax_events", "mode_change_log",
    }
    assert set(HASH_PAYLOAD_COLUMNS.keys()) == expected


async def test_insert_with_chain_raises_for_unknown_table(sqlite_session):
    """Per Correction 1 — fail-secure on the unknown-table branch.

    A caller passing an unregistered table name is a bug; we want it
    surfaced loudly, not silently hashed-and-forgotten.
    """
    with pytest.raises(ValueError, match="not in HASH_PAYLOAD_COLUMNS"):
        await insert_with_chain(
            sqlite_session, "some_unregistered_table", {"foo": "bar"},
        )
