"""PR3 integration: dual-lane shadow worker — same-symbol cross-TF.

Spec §6.2 + R2: a LONG signal firing on 1h AND 15m for BTC near-
simultaneously must produce **two distinct `shadow_trades` rows** with
different `timeframe` values. The promotion-gate filter must NOT
silently de-dup cross-TF trades — both lanes are independent signal
generators.

This test drives a multi-TF `ShadowWorker` end-to-end with a fake
multi-stream reader that yields one entry-eligible candle on each TF
in sequence, then asserts:
  - 2 open positions persisted (one per TF on same symbol)
  - per-(symbol, tf) cooldown / open-positions cache correctly populated
  - persistence routes the row through with timeframe filled

The signal-eval path is mocked so we don't need a fully-warm bars
buffer / real predictor — the test scope is the worker's TF-aware
state machine, not the predictor's accuracy.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shadow.engine import Direction
from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class _FakeReader:
    """Async iterator that yields a fixed list of candles then completes."""

    def __init__(self, candles: list[MultiStreamCandle]) -> None:
        self._candles = candles

    async def stream(self):  # type: ignore[no-untyped-def]
        for c in self._candles:
            yield c


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


def _candle(*, timeframe: str, close: float = 100.0) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol="BTCUSDT", timeframe=timeframe, ts=_NOW,
        open=close * 0.999, high=close * 1.001,
        low=close * 0.998, close=close, volume=1000.0,
    )


def _seed_bars() -> pd.DataFrame:
    idx = pd.date_range(
        end=_NOW, periods=200, freq="1h",
    )
    return pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1000.0,
    }, index=idx)


@pytest.mark.asyncio
async def test_dual_lane_same_symbol_produces_two_open_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The motivating PR3 scenario: same symbol fires LONG on both 1h
    AND 15m → two shadow_open_positions rows (one per TF). 1h does NOT
    block 15m even though they share the symbol."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.shadow.engine import ShadowSignal

    # Mock the evaluator to always return a LONG signal — we want the
    # entry path to fire for every candle the worker processes.
    fake_signal_1h = ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, ts=_NOW,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        score=0.5, confidence=0.7, atr=1.5,
        layer_scores={}, signal_id="sig_1h",
    )
    fake_signal_15m = ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, ts=_NOW,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        score=0.5, confidence=0.7, atr=1.5,
        layer_scores={}, signal_id="sig_15m",
    )

    eval_calls: list[str] = []

    def _fake_evaluate(*, symbol: str, score: float, confidence: float,
                       last_close: float, atr: float,
                       layer_scores: dict, ts: Any) -> Any:
        # Per-call we don't know the TF; tag by call count.
        call_idx = len(eval_calls)
        eval_calls.append(symbol)
        return fake_signal_1h if call_idx == 0 else fake_signal_15m

    fake_pred = MagicMock()
    fake_pred.layer_scores = {}
    fake_pred.final = MagicMock(score=0.5, confidence=0.7)
    fake_pred.mtf_agreement = None
    fake_pred.symbol = "BTCUSDT"

    fake_cache = MagicMock()
    fake_cache.get_or_load = AsyncMock(return_value=None)

    candles = [
        _candle(timeframe="1h", close=100.0),
        _candle(timeframe="15m", close=100.0),
    ]

    w = ShadowWorker(
        symbols=["BTCUSDT"], session_factory=factory,
        reader=_FakeReader([candles[0]]),  # 1h-stream reader
        timeframes=["1h", "15m"],
        readers={
            "1h": _FakeReader([candles[0]]),
            "15m": _FakeReader([candles[1]]),
        },
        seed_history={
            ("BTCUSDT", "1h"): _seed_bars(),
            ("BTCUSDT", "15m"): _seed_bars(),
        },
    )

    from app.ops import pause_state

    async def _not_paused() -> bool:
        return False

    monkeypatch.setattr(pause_state, "is_paused", _not_paused)

    # Patch evaluator + heavy I/O so the entry path is deterministic
    w.evaluator = MagicMock()
    w.evaluator.evaluate = MagicMock(side_effect=_fake_evaluate)
    w.evaluator.long_threshold = 0.30
    w.evaluator.short_threshold = -0.30
    w.evaluator.min_confidence = 0.50

    with patch("app.shadow.worker.build_prediction",
               new=AsyncMock(return_value=fake_pred)), \
         patch("app.shadow.worker.pattern_stats_cache", new=fake_cache), \
         patch("app.shadow.worker._atr", new=MagicMock(return_value=1.5)), \
         patch("app.shadow.worker.record_heartbeat",
               new=AsyncMock(return_value=None)), \
         patch("app.shadow.worker.build_obs_components",
               new=AsyncMock(return_value={})), \
         patch("app.shadow.worker.persist_observation",
               new=AsyncMock(return_value=None)), \
         patch("app.shadow.worker.shadow_updates.publish_position_opened",
               new=AsyncMock(return_value=None)):
        # Run the worker — it spawns one consumer per TF; both finish quickly
        # since each FakeReader yields exactly one candle then closes.
        try:
            await asyncio.wait_for(w.run(), timeout=5.0)
        except asyncio.CancelledError:
            pass

    # 2 open positions in DB — one per TF
    async with AsyncSession(engine) as s:
        rows = (await s.execute(sa.text(
            "SELECT symbol, timeframe FROM shadow_open_positions "
            "ORDER BY timeframe"
        ))).all()
    assert len(rows) == 2
    timeframes = {r.timeframe for r in rows}
    assert timeframes == {"1h", "15m"}
    symbols = {r.symbol for r in rows}
    assert symbols == {"BTCUSDT"}  # same symbol, distinct TFs


@pytest.mark.asyncio
async def test_dual_lane_in_memory_state_keyed_per_tf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the entry sequence, worker's open_positions dict is keyed by
    (symbol, tf) — both lanes coexist in memory."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.shadow.engine import ShadowSignal

    fake_signal = ShadowSignal(
        symbol="BTCUSDT", direction=Direction.LONG, ts=_NOW,
        entry_price=100.0, stop_loss=98.0, take_profit=104.0,
        score=0.5, confidence=0.7, atr=1.5,
        layer_scores={}, signal_id="sig_dual",
    )

    fake_pred = MagicMock()
    fake_pred.layer_scores = {}
    fake_pred.final = MagicMock(score=0.5, confidence=0.7)
    fake_pred.mtf_agreement = None

    fake_cache = MagicMock()
    fake_cache.get_or_load = AsyncMock(return_value=None)

    w = ShadowWorker(
        symbols=["BTCUSDT"], session_factory=factory,
        reader=_FakeReader([]),
        timeframes=["1h", "15m"],
        readers={
            "1h": _FakeReader([_candle(timeframe="1h")]),
            "15m": _FakeReader([_candle(timeframe="15m")]),
        },
        seed_history={
            ("BTCUSDT", "1h"): _seed_bars(),
            ("BTCUSDT", "15m"): _seed_bars(),
        },
    )

    from app.ops import pause_state

    async def _not_paused() -> bool:
        return False

    monkeypatch.setattr(pause_state, "is_paused", _not_paused)

    w.evaluator = MagicMock()
    w.evaluator.evaluate = MagicMock(return_value=fake_signal)
    w.evaluator.long_threshold = 0.30
    w.evaluator.short_threshold = -0.30
    w.evaluator.min_confidence = 0.50

    with patch("app.shadow.worker.build_prediction",
               new=AsyncMock(return_value=fake_pred)), \
         patch("app.shadow.worker.pattern_stats_cache", new=fake_cache), \
         patch("app.shadow.worker._atr", new=MagicMock(return_value=1.5)), \
         patch("app.shadow.worker.record_heartbeat",
               new=AsyncMock(return_value=None)), \
         patch("app.shadow.worker.build_obs_components",
               new=AsyncMock(return_value={})), \
         patch("app.shadow.worker.persist_observation",
               new=AsyncMock(return_value=None)), \
         patch("app.shadow.worker.shadow_updates.publish_position_opened",
               new=AsyncMock(return_value=None)):
        try:
            await asyncio.wait_for(w.run(), timeout=5.0)
        except asyncio.CancelledError:
            pass

    # In-memory state: both (BTCUSDT, '1h') and (BTCUSDT, '15m') keys
    assert ("BTCUSDT", "1h") in w.open_positions
    assert ("BTCUSDT", "15m") in w.open_positions
    # Both positions stamped with the correct per-TF timeframe
    assert w.open_positions[("BTCUSDT", "1h")].timeframe == "1h"
    assert w.open_positions[("BTCUSDT", "15m")].timeframe == "15m"
