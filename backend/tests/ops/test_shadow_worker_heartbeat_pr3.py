"""PR3 spec §4.7 (B6): shadow_worker heartbeats from _handle_candle.

FU-1 originally wired the heartbeat call inside _handle_candle. PR3
Phase 4.1 refactored the worker to be TF-aware. This test asserts
the heartbeat path survives the refactor: one record_heartbeat() call
fires per processed candle, status="ok", details include the TF.

Companion to tests/ops/test_shadow_worker_pr3_registry.py — that file
covers the registry side (max_staleness_seconds=30*60,
pending_heartbeat=False). This file covers the runtime side (the
call actually fires).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.shadow.multi_stream import MultiStreamCandle
from app.shadow.worker import ShadowWorker


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _candle(symbol: str = "BTCUSDT", timeframe: str = "1h",
            close: float = 100.0) -> MultiStreamCandle:
    return MultiStreamCandle(
        symbol=symbol, timeframe=timeframe,
        ts=_NOW, open=close * 0.999, high=close * 1.001,
        low=close * 0.998, close=close, volume=1000.0,
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


@pytest.mark.asyncio
async def test_handle_candle_fires_heartbeat_on_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive one 1h candle through _handle_candle; record_heartbeat
    should be called once with status='ok' + tf details."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"], session_factory=factory,
        reader=MagicMock(), timeframes=["1h"],
    )

    from app.ops import pause_state

    async def _not_paused() -> bool:
        return False

    monkeypatch.setattr(pause_state, "is_paused", _not_paused)

    heartbeat_mock = AsyncMock(return_value=None)
    # Patch heavy I/O so _maybe_open_position bails fast (no-signal path)
    fake_pred = MagicMock()
    fake_pred.layer_scores = {}
    fake_pred.final = MagicMock(score=0.0, confidence=0.0)
    fake_pred.mtf_agreement = None

    fake_cache = MagicMock()
    fake_cache.get_or_load = AsyncMock(return_value=None)

    with patch("app.shadow.worker.record_heartbeat", new=heartbeat_mock), \
         patch("app.shadow.worker.build_prediction",
               new=AsyncMock(return_value=fake_pred)), \
         patch("app.shadow.worker.pattern_stats_cache", new=fake_cache), \
         patch("app.shadow.worker._atr", new=MagicMock(return_value=1.5)):
        await w._handle_candle(_candle(timeframe="1h"), tf="1h")  # noqa: SLF001

    # Exactly one heartbeat fired, status=ok, tf in details
    heartbeat_mock.assert_called_once()
    args, kwargs = heartbeat_mock.call_args
    assert args[1] == "shadow_worker"
    assert kwargs["status"] == "ok"
    assert kwargs["details"]["tf"] == "1h"
    assert kwargs["details"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_handle_candle_fires_heartbeat_with_15m_tf_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """15m candle → heartbeat details include tf='15m'. Watchdog reads this
    so an operator can see which TF stream is keeping shadow_worker alive."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"], session_factory=factory,
        reader=MagicMock(), timeframes=["1h", "15m"],
    )

    from app.ops import pause_state

    async def _not_paused() -> bool:
        return False

    monkeypatch.setattr(pause_state, "is_paused", _not_paused)

    heartbeat_mock = AsyncMock(return_value=None)
    fake_pred = MagicMock()
    fake_pred.layer_scores = {}
    fake_pred.final = MagicMock(score=0.0, confidence=0.0)
    fake_pred.mtf_agreement = None
    fake_cache = MagicMock()
    fake_cache.get_or_load = AsyncMock(return_value=None)

    with patch("app.shadow.worker.record_heartbeat", new=heartbeat_mock), \
         patch("app.shadow.worker.build_prediction",
               new=AsyncMock(return_value=fake_pred)), \
         patch("app.shadow.worker.pattern_stats_cache", new=fake_cache), \
         patch("app.shadow.worker._atr", new=MagicMock(return_value=1.5)):
        await w._handle_candle(_candle(timeframe="15m"), tf="15m")  # noqa: SLF001

    heartbeat_mock.assert_called_once()
    kwargs = heartbeat_mock.call_args.kwargs
    assert kwargs["details"]["tf"] == "15m"


@pytest.mark.asyncio
async def test_handle_candle_paused_path_still_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused-state path emits a heartbeat with details={"paused": True}
    so the watchdog can distinguish "worker alive but paused" from
    "worker silent". Locks in the FU-1 follow-up fix that dropped
    candle.symbol from the paused details payload."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    w = ShadowWorker(
        symbols=["BTCUSDT"], session_factory=factory,
        reader=MagicMock(), timeframes=["1h"],
    )

    from app.ops import pause_state

    async def _paused() -> bool:
        return True

    monkeypatch.setattr(pause_state, "is_paused", _paused)

    heartbeat_mock = AsyncMock(return_value=None)
    with patch("app.shadow.worker.record_heartbeat", new=heartbeat_mock):
        await w._handle_candle(_candle(timeframe="1h"), tf="1h")  # noqa: SLF001

    heartbeat_mock.assert_called_once()
    kwargs = heartbeat_mock.call_args.kwargs
    assert kwargs["status"] == "ok"
    assert kwargs["details"] == {"paused": True}


def test_registry_aligns_with_runtime_30min_budget() -> None:
    """Lock-in: max_staleness_seconds matches the spec §4.7 + §6.3 budget.
    Mirrors test_shadow_worker_pr3_registry.py; included here so the
    runtime + registry contracts can't drift in opposite directions
    silently. If a future PR changes one side, this test catches it."""
    from app.ops.worker_registry import by_name
    spec = by_name("shadow_worker")
    assert spec is not None
    assert spec.max_staleness_seconds == 30 * 60
    assert spec.pending_heartbeat is False
