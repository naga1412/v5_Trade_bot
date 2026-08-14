"""PR3 Phase 4: ShadowWorker TF-aware refactor.

Tests the new contract:
  - ShadowWorker accepts `timeframes: list[str]` (default ["1h"] preserves pre-PR3 behavior)
  - `bars` keyed by `(symbol, tf)` tuples (was: keyed by symbol only)
  - `open_positions` keyed by `(symbol, tf)` (was: keyed by symbol)
  - `cooldowns` keyed by `(symbol, tf)` (was: keyed by symbol)
  - `_handle_candle(candle, tf)` is TF-aware; appending routes to the
    correct buffer; per-TF cooldowns + opens checked independently

Test-only fixtures: in-memory engine matching the post-PR3 schema, fake
reader that yields candles directly so we drive `_handle_candle` without
spinning a real WS stream.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.shadow.engine import Direction, ShadowPosition
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _stub_flow_features_fire_and_forget(monkeypatch: pytest.MonkeyPatch) -> None:
    """_handle_candle fires app.core.features.flow_features as an un-awaited
    background task on every 1h candle (W4) — irrelevant to the TF-routing
    behavior this file tests, and left real it touches the process-global
    Binance Futures RateLimitedClient singleton (app.data.adapters) across
    this test's event loop, which then breaks unrelated tests reusing that
    singleton in a later, different event loop (e.g. test_adapter_registry.py).
    """
    monkeypatch.setattr(
        "app.core.features.flow_features.update_flow_cache_and_persist",
        AsyncMock(return_value=None),
    )


async def _mk_engine() -> Any:
    """Post-PR3 schema (matches alembic 0021)."""
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


class _FakeReader:
    """Yields a fixed list of candles then completes."""

    def __init__(self, candles: list[MultiStreamCandle]) -> None:
        self._candles = candles

    async def stream(self):  # pragma: no cover - simple async generator
        for c in self._candles:
            yield c


def _candle(*, symbol: str = "BTCUSDT", timeframe: str = "1h",
            close: float = 100.0, ts: datetime | None = None) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts or _NOW,
        open=close * 0.999, high=close * 1.001,
        low=close * 0.998, close=close, volume=1000.0,
    )


# --- Construction with timeframes -----------------------------------------


def test_worker_constructs_with_single_timeframe_default() -> None:
    """Pre-PR3 callers (no `timeframes` kwarg) get the legacy 1h-only worker."""
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=async_sessionmaker(),  # type: ignore[call-arg]
        reader=_FakeReader([]),
    )
    assert w.timeframes == ["1h"]


def test_worker_constructs_with_explicit_timeframes() -> None:
    """PR3: caller passes timeframes=['1h', '15m'] → worker stores both."""
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=async_sessionmaker(),  # type: ignore[call-arg]
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
    )
    assert w.timeframes == ["1h", "15m"]


# --- Per-(symbol, tf) bars buffer -----------------------------------------


@pytest.mark.asyncio
async def test_handle_candle_keys_bars_by_symbol_and_tf() -> None:
    """Two candles same symbol different TFs → two distinct bars buffers."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
    )
    # Bypass setup() (no REST seed in this test).
    await w._handle_candle(_candle(timeframe="1h", close=100.0), tf="1h")  # noqa: SLF001
    await w._handle_candle(_candle(timeframe="15m", close=200.0), tf="15m")  # noqa: SLF001

    # bars dict is keyed by (symbol, tf)
    assert ("BTCUSDT", "1h") in w.bars
    assert ("BTCUSDT", "15m") in w.bars
    # 1h buffer has close=100, 15m has close=200 — they're independent
    assert w.bars[("BTCUSDT", "1h")]["close"].iloc[-1] == 100.0
    assert w.bars[("BTCUSDT", "15m")]["close"].iloc[-1] == 200.0


# --- Per-(symbol, tf) open positions --------------------------------------


@pytest.mark.asyncio
async def test_open_positions_keyed_by_symbol_and_tf() -> None:
    """A 1h BTC position does NOT block a 15m BTC open. The PR3 motivation."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
    )
    # Manually seed open_positions to simulate a 1h BTC position already open.
    p1h = ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        position_size_usdt=10.0, entry_score=0.4, entry_confidence=0.6,
        entry_atr=1.5, layer_scores={}, bars_held=0,
        opened_at=_NOW, last_check_at=_NOW, signal_id="sig_1h",
        timeframe="1h",
    )
    w.open_positions[("BTCUSDT", "1h")] = p1h

    # 1h symbol routes to close-check
    assert ("BTCUSDT", "1h") in w.open_positions
    # 15m on same symbol is NOT in open_positions — would route to open-check
    assert ("BTCUSDT", "15m") not in w.open_positions


# --- Per-(symbol, tf) cooldowns -------------------------------------------


@pytest.mark.asyncio
async def test_cooldowns_keyed_by_symbol_and_tf() -> None:
    """1h cooldown on BTC does NOT block 15m entry on BTC."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
    )
    until = _NOW + timedelta(hours=1)
    w.cooldowns[("BTCUSDT", "1h")] = until

    assert w.cooldowns.get(("BTCUSDT", "1h")) == until
    assert w.cooldowns.get(("BTCUSDT", "15m")) is None


# --- _handle_candle TF-aware error heartbeat ------------------------------


@pytest.mark.asyncio
async def test_handle_candle_threads_tf_through_pause_path(monkeypatch) -> None:
    """Pause-state path still heartbeats but doesn't process the candle.
    Just confirms the tf arg doesn't break the early-return path."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=factory,
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
    )

    from app.ops import pause_state

    async def _is_paused_yes() -> bool:
        return True

    monkeypatch.setattr(pause_state, "is_paused", _is_paused_yes)
    # Should NOT raise even with the new tf arg
    await w._handle_candle(  # noqa: SLF001
        _candle(timeframe="15m"), tf="15m",
    )
    # No bars written (paused short-circuits before _append_bar)
    assert w.bars == {}
