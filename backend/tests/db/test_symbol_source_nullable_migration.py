"""Migration 0042: symbol_source drops NOT NULL DEFAULT on both
shadow_open_positions and shadow_trades -- item 0 (2026-08-30), the
schema half of the NO-DEFAULT-ON-FAILURE ruling.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — migration tests are CI-only.",
)

_TABLES = ("shadow_open_positions", "shadow_trades")


@pytest.mark.asyncio
async def test_symbol_source_is_nullable_with_no_default() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        for table in _TABLES:
            row = (await conn.execute(sa.text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = 'symbol_source'"
            ), {"t": table})).one()
            assert row.is_nullable == "YES", f"{table}.symbol_source should be nullable"
            assert row.column_default is None, (
                f"{table}.symbol_source should have no DEFAULT — a lingering "
                f"'established_top20' default is exactly the fallback this "
                f"migration exists to remove"
            )


@pytest.mark.asyncio
async def test_symbol_source_null_insert_round_trips_on_shadow_trades() -> None:
    """The real proof: a NULL actually inserts and reads back as NULL,
    not just that the schema says it should. Uses a throwaway row with a
    highly distinctive signal_id, explicitly deleted in a finally block
    — no data left behind, and cleanup runs even if the assertion
    fails."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    _SIGNAL_ID = "zzz_symbol_source_null_test"
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO shadow_trades ("
                "user_id, symbol, timeframe, direction, entry_price, stop_loss, "
                "take_profit, position_size_usdt, entry_score, entry_confidence, "
                "layer_scores, entry_atr, opened_at, inputs_hash, model_version, "
                "signal_id, symbol_source, prev_hash, row_hash"
                ") VALUES ("
                "1, 'ZZZTESTNULL', '1h', 'LONG', 1.0, 0.9, 1.1, 30.0, 0.4, 0.6, "
                "'{}', 0.01, now(), 'test_hash', 'test', "
                ":sig, NULL, repeat('0', 64), repeat('1', 64))"
            ), {"sig": _SIGNAL_ID})
        async with engine.begin() as conn:
            row = (await conn.execute(sa.text(
                "SELECT symbol_source FROM shadow_trades WHERE signal_id = :sig"
            ), {"sig": _SIGNAL_ID})).one()
            assert row.symbol_source is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "DELETE FROM shadow_trades WHERE signal_id = :sig"
            ), {"sig": _SIGNAL_ID})
