"""PR-CLEANUP-BATCH-1 §I — shadow_worker subscription set = top_30 ∪ open_positions.

These tests verify:
  1. UUSDT no longer in SHADOW_SPOT_BLACKLIST (was added in PR10.7; verified
     priceable on Binance SPOT 2026-05-22T07:03Z, so it's stale).
  2. The subscription helper unions top-30 with open-position symbols so a
     position on a symbol that's been dropped from the top-30 universe still
     receives WS ticks (TRUMPUSDT-class orphan from PR-DIAG-AUDIT-BUNDLE).

We test the helper `_load_open_position_symbols` against a real in-memory
SQLite session so it exercises the actual SQL path that production uses.
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _build_in_memory_engine() -> Any:
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _create_open_positions_table(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL NOT NULL, stop_loss REAL NOT NULL, "
            "take_profit REAL NOT NULL, position_size_usdt REAL NOT NULL, "
            "entry_score REAL NOT NULL, entry_confidence REAL NOT NULL, "
            "entry_atr REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0, "
            "opened_at TEXT NOT NULL, last_check_at TEXT NOT NULL, "
            "signal_id TEXT NOT NULL UNIQUE)"
        ))


async def _insert_open_position(
    factory: async_sessionmaker, *, symbol: str, signal_id: str,
) -> None:
    async with factory() as s:
        await s.execute(sa.text(
            "INSERT INTO shadow_open_positions ("
            "user_id, symbol, direction, entry_price, stop_loss, take_profit, "
            "position_size_usdt, entry_score, entry_confidence, entry_atr, "
            "opened_at, last_check_at, signal_id) "
            "VALUES (1, :sym, 'LONG', 100.0, 95.0, 110.0, 30.0, "
            "0.5, 0.7, 1.0, '2026-05-22T00:00:00+00:00', "
            "'2026-05-22T00:00:00+00:00', :sig)"
        ), {"sym": symbol, "sig": signal_id})
        await s.commit()


def test_uusdt_no_longer_in_blacklist() -> None:
    """PR-CLEANUP-BATCH-1 §I removed UUSDT from SHADOW_SPOT_BLACKLIST.

    Confirmed priceable on Binance SPOT 2026-05-22T07:03Z via
    /api/v3/ticker/price?symbol=UUSDT.
    """
    from app.config import get_settings
    bl = get_settings().SHADOW_SPOT_BLACKLIST
    assert "UUSDT" not in bl
    # Sanity: the four original PR10.7 entries are still blacklisted.
    for sym in ("EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT"):
        assert sym in bl, f"{sym} should still be blacklisted"
    # 2026-07-11 hotfix: 7 stablecoin/pegged entries added to main, now backported to dev.
    # 2026-07-18: USDCUSDT added (confirmed stablecoin, avg ATR 0.007%).
    for sym in ("RLUSDUSDT", "USD1USDT", "USDEUSDT", "SPCXBUSDT",
                "EURUSDT", "FDUSDUSDT", "SNDKBUSDT", "USDCUSDT"):
        assert sym in bl, f"{sym} should be blacklisted (stablecoin)"
    assert len(bl) == 12


@pytest.mark.asyncio
async def test_load_open_position_symbols_returns_distinct_set() -> None:
    """Helper returns {symbol} for every distinct symbol with an open position."""
    from app.shadow.worker import _load_open_position_symbols
    engine = _build_in_memory_engine()
    await _create_open_positions_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_open_position(factory, symbol="BTCUSDT", signal_id="sig1")
    await _insert_open_position(factory, symbol="TRUMPUSDT", signal_id="sig2")

    async with factory() as s:
        result = await _load_open_position_symbols(s)
    assert result == {"BTCUSDT", "TRUMPUSDT"}


@pytest.mark.asyncio
async def test_subscription_includes_open_position_symbols_outside_top30() -> None:
    """top_30 + an open position on RANDOMUSDT (not in top-30) → union includes it."""
    from app.shadow.worker import (
        _compute_subscription_set,
        _load_open_position_symbols,
    )
    engine = _build_in_memory_engine()
    await _create_open_positions_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_open_position(factory, symbol="RANDOMUSDT", signal_id="sigR")

    top_30 = [f"SYM{i}USDT" for i in range(30)]
    async with factory() as s:
        op_syms = await _load_open_position_symbols(s)
    subscription = _compute_subscription_set(top_30, op_syms)
    assert "RANDOMUSDT" in subscription
    assert len(subscription) == 31  # 30 + 1 extra
    # Deduped + sorted for stability.
    assert subscription == sorted(set(subscription))


@pytest.mark.asyncio
async def test_subscription_dedupes_when_symbol_in_both_top30_and_open_position() -> None:
    """Open position on BTCUSDT (also in top-30) → BTCUSDT appears once."""
    from app.shadow.worker import (
        _compute_subscription_set,
        _load_open_position_symbols,
    )
    engine = _build_in_memory_engine()
    await _create_open_positions_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_open_position(factory, symbol="BTCUSDT", signal_id="sigBTC")

    top_30 = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    async with factory() as s:
        op_syms = await _load_open_position_symbols(s)
    subscription = _compute_subscription_set(top_30, op_syms)
    assert subscription.count("BTCUSDT") == 1
    assert len(subscription) == 3


@pytest.mark.asyncio
async def test_no_open_positions_subscription_equals_top30() -> None:
    """Regression: zero open positions → subscription is sorted(top_30)."""
    from app.shadow.worker import (
        _compute_subscription_set,
        _load_open_position_symbols,
    )
    engine = _build_in_memory_engine()
    await _create_open_positions_table(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    top_30 = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    async with factory() as s:
        op_syms = await _load_open_position_symbols(s)
    subscription = _compute_subscription_set(top_30, op_syms)
    assert subscription == sorted(top_30)
