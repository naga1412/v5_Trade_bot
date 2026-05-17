"""PR3 Phase 4.3: ShadowWorker.setup() reuses PR1's _KLINE_CACHE.

Spec §4.3 D1/D2/D3:
  - When the MTF cache has ≥SHADOW_PREWARM_BARS klines for (sym, tf),
    setup() uses them directly (no REST fetch for that buffer).
  - When the cache is cold, setup() REST-fetches AND best-effort
    populates the MTF cache so subsequent MTF compute hits warm.
  - Single source of truth for (sym, tf) klines across PR1 + PR3.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.scoring import mtf_confluence
from app.shadow.worker import (
    ShadowWorker,
    _candles_to_raw_klines,
    _klines_to_dataframe,
)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE shadow_cooldowns ("
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "cooldown_until TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol, timeframe))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE shadow_open_positions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 1, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL DEFAULT '1h', "
            "direction TEXT NOT NULL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size_usdt REAL, "
            "entry_score REAL, entry_confidence REAL, entry_atr REAL, "
            "bars_held INTEGER DEFAULT 0, "
            "opened_at TEXT, last_check_at TEXT, signal_id TEXT, "
            "UNIQUE (symbol, timeframe))"
        ))
    return engine


def _make_kline_rows(n: int, close_base: float = 100.0) -> list[list[Any]]:
    """N synthetic Binance kline rows (raw list-of-list format)."""
    rows: list[list[Any]] = []
    base_ts = 1_700_000_000_000  # ms
    for i in range(n):
        ts = base_ts + i * 3_600_000  # 1h apart
        close = close_base + i * 0.1
        rows.append([
            ts,
            str(close - 0.5), str(close + 1.0), str(close - 1.0), str(close),
            "1000.0",
            ts + 3_599_999, "100000.0", 10, "500.0", "50000.0", "0",
        ])
    return rows


@pytest.mark.asyncio
async def test_setup_reuses_warm_mtf_cache_and_skips_rest_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache warm with ≥HISTORY_BARS klines → setup() uses them; no
    BinanceClient.fetch_klines call for that (sym, tf)."""
    from app.shadow.worker import HISTORY_BARS

    # Pre-populate MTF cache with enough klines for the worker
    mtf_confluence._KLINE_CACHE.clear()
    klines = _make_kline_rows(HISTORY_BARS + 10)
    mtf_confluence._cache_set(
        "BTCUSDT", "1h", klines, fetched_at=time.time(),
    )

    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=MagicMock(),
        timeframes=["1h"],
    )

    # Track whether BinanceClient.fetch_klines was called
    fetch_mock = AsyncMock(return_value=[])
    with patch("app.shadow.worker.BinanceClient") as MockClient:
        MockClient.return_value.fetch_klines = fetch_mock
        await w.setup()

    assert ("BTCUSDT", "1h") in w.bars
    assert len(w.bars[("BTCUSDT", "1h")]) == HISTORY_BARS + 10
    # REST fetch NOT called for the cache-hit symbol
    fetch_mock.assert_not_called()

    mtf_confluence._KLINE_CACHE.clear()


@pytest.mark.asyncio
async def test_setup_cache_miss_rest_fetches_and_populates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache cold → setup() REST-fetches AND populates the MTF cache
    so PR1's MTF compute hits warm on the next call."""
    from app.shadow.worker import HISTORY_BARS

    mtf_confluence._KLINE_CACHE.clear()

    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["ETHUSDT"],
        session_factory=factory,
        reader=MagicMock(),
        timeframes=["1h"],
    )

    # Fake Candle objects matching the worker's expected shape. SimpleNamespace
    # gives us instance-attrs that flow through __dict__ correctly (the worker
    # does `pd.DataFrame([c.__dict__ for c in history])`).
    from types import SimpleNamespace
    fake_candles = [
        SimpleNamespace(
            ts=datetime(2026, 5, 18, hour=h, tzinfo=timezone.utc),
            open=100.0 + h * 0.1, high=101.0 + h * 0.1,
            low=99.0 + h * 0.1, close=100.5 + h * 0.1,
            volume=1000.0,
        )
        for h in range(min(HISTORY_BARS, 23))  # < 24 to stay under hour bounds
    ]

    fetch_mock = AsyncMock(return_value=fake_candles)
    with patch("app.shadow.worker.BinanceClient") as MockClient:
        MockClient.return_value.fetch_klines = fetch_mock
        await w.setup()

    # REST fetch WAS called since cache was cold
    fetch_mock.assert_called_once()
    # Bars buffer populated
    assert ("ETHUSDT", "1h") in w.bars
    # Best-effort: MTF cache now warm for this (sym, tf)
    cached = mtf_confluence._cache_get("ETHUSDT", "1h")
    assert cached is not None
    assert len(cached) == len(fake_candles)

    mtf_confluence._KLINE_CACHE.clear()


@pytest.mark.asyncio
async def test_setup_cache_with_insufficient_klines_falls_back_to_rest() -> None:
    """Cache has < HISTORY_BARS klines (partial seed) → spec says treat
    as miss + REST-fetch (cache reuse requires ≥SHADOW_PREWARM_BARS)."""
    from app.shadow.worker import HISTORY_BARS

    mtf_confluence._KLINE_CACHE.clear()
    # Half the required count — should NOT be reused
    mtf_confluence._cache_set(
        "BTCUSDT", "1h", _make_kline_rows(HISTORY_BARS // 2),
        fetched_at=time.time(),
    )

    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=MagicMock(),
        timeframes=["1h"],
    )

    fetch_mock = AsyncMock(return_value=[])
    with patch("app.shadow.worker.BinanceClient") as MockClient:
        MockClient.return_value.fetch_klines = fetch_mock
        await w.setup()

    fetch_mock.assert_called_once()
    mtf_confluence._KLINE_CACHE.clear()


# --- Helper function unit tests --------------------------------------------


def test_klines_to_dataframe_shape() -> None:
    """Helper produces an OHLCV-indexed DataFrame from raw Binance klines."""
    klines = _make_kline_rows(5)
    df = _klines_to_dataframe(klines)
    assert len(df) == 5
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "ts"


def test_klines_to_dataframe_empty_input_returns_empty_frame() -> None:
    """Empty kline list → empty DataFrame with the right columns."""
    df = _klines_to_dataframe([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_candles_to_raw_klines_round_trip() -> None:
    """Candle → raw kline → Candle (close field round-trips)."""
    from types import SimpleNamespace
    fake = SimpleNamespace(
        ts=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        open=100.0, high=101.0, low=99.0,
        close=100.5, volume=1000.0,
    )
    raw = _candles_to_raw_klines([fake])
    assert len(raw) == 1
    row = raw[0]
    # Position 4 = close (Binance kline schema)
    assert float(row[4]) == 100.5
    # Position 5 = volume
    assert float(row[5]) == 1000.0
