"""FU-28 ui_freshness_monitor — 4 branches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _settings(*, threshold: int = 1800, auto_recycle: bool = False):
    return SimpleNamespace(
        FU28_POLL_INTERVAL_SECONDS=300,
        FU28_STALE_PNL_TICK_THRESHOLD_SECONDS=threshold,
        FU28_AUTO_RECYCLE_ENABLED=auto_recycle,
    )


def _session_factory() -> MagicMock:
    """Mock async_sessionmaker — calling it returns an async-context-manager
    that yields a stub AsyncSession. ``list_open_positions`` is patched so
    the yielded session doesn't actually need to do anything."""
    sf = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=None)
    sf.return_value = ctx
    return sf


@pytest.mark.asyncio
async def test_no_open_positions_returns_ok() -> None:
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ) as hb:
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(), now_fn=lambda: _NOW,
        )
    assert result["stale"] is False
    _args, kwargs = hb.call_args
    assert kwargs["status"] == "ok"


@pytest.mark.asyncio
async def test_fresh_tick_returns_ok() -> None:
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [SimpleNamespace(symbol="BTCUSDT")]
    last_emit = {"BTCUSDT": _NOW - timedelta(minutes=10)}  # 600s < 1800
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value=last_emit,
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ) as hb:
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(), now_fn=lambda: _NOW,
        )
    assert result["stale"] is False
    _args, kwargs = hb.call_args
    assert kwargs["status"] == "ok"


@pytest.mark.asyncio
async def test_stale_tick_returns_degraded_no_recycle() -> None:
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [SimpleNamespace(symbol="BTCUSDT")]
    last_emit = {"BTCUSDT": _NOW - timedelta(hours=2)}  # 7200s > 1800
    recycle = AsyncMock()
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value=last_emit,
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ) as hb:
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(auto_recycle=False),
            now_fn=lambda: _NOW, recycle_fn=recycle,
        )
    assert result["stale"] is True
    _args, kwargs = hb.call_args
    assert kwargs["status"] == "degraded"
    recycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_tick_with_auto_recycle_calls_recycle() -> None:
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [SimpleNamespace(symbol="BTCUSDT")]
    last_emit = {"BTCUSDT": _NOW - timedelta(hours=2)}
    recycle = AsyncMock()
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value=last_emit,
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ):
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(auto_recycle=True),
            now_fn=lambda: _NOW, recycle_fn=recycle,
        )
    assert result["stale"] is True
    recycle.assert_awaited_once_with("shadow_worker")
