"""FU-28 ui_freshness_monitor — 4 branches.

PR-CLEANUP-BATCH-1 §H extension: per-symbol missing/stale tracking + resubscribe
metric on 3-events-in-10-min sliding window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _settings(
    *,
    threshold: int = 1800,
    auto_recycle: bool = False,
    blacklist: list[str] | None = None,
):
    return SimpleNamespace(
        FU28_POLL_INTERVAL_SECONDS=300,
        FU28_STALE_PNL_TICK_THRESHOLD_SECONDS=threshold,
        FU28_AUTO_RECYCLE_ENABLED=auto_recycle,
        SHADOW_SPOT_BLACKLIST=blacklist if blacklist is not None else [],
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


# ---------- PR-CLEANUP-BATCH-1 §H extensions ----------------------------
#
# Per-symbol completeness: distinguish between symbols that *never* emitted
# a pnl_tick (missing) vs symbols whose emit is older than threshold (stale).
# The pre-fix code silently filtered never-emitting symbols out via the
# `relevant_emits = [last_emit[s] for s in symbols_open if s in last_emit]`
# list comprehension, so a SPOT subscription that never fires (e.g. dropped
# from universe, or a brand-new open position before the first tick) would
# leave the healer reporting "ok" with newest_age=None silently.
#
# Held-position grace: positions opened <5 min ago do NOT count as
# missing — the first pnl_tick can lag the open by one bar.


def _position(symbol: str, opened_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, opened_at=opened_at)


@pytest.mark.asyncio
async def test_blacklisted_symbol_null_price_does_not_trigger() -> None:
    """Open position on UUSDT (in SHADOW_SPOT_BLACKLIST), no emit, held 1h.

    Expected: UUSDT NOT in missing_symbols (it's blacklisted so we know
    upstream why it'll never emit; flagging it would be noise).
    """
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    # 1h ago — well past the 5-min grace.
    open_pos = [_position("UUSDT", _NOW - timedelta(hours=1))]
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},  # no emits
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ) as hb:
        result = await run_one_freshness_check(
            session_factory=sf,
            settings=_settings(blacklist=["UUSDT"]),
            now_fn=lambda: _NOW,
        )
    assert "UUSDT" not in result.get("missing_symbols", [])
    # Heartbeat details should also reflect this.
    _args, kwargs = hb.call_args
    assert "UUSDT" not in (kwargs.get("details", {}).get("missing_symbols") or [])


@pytest.mark.asyncio
async def test_non_blacklisted_null_price_after_5min_triggers() -> None:
    """Open position on BTCUSDT (not blacklisted), no emit, held 6 min.

    Expected: BTCUSDT in missing_symbols; status=degraded.
    """
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [_position("BTCUSDT", _NOW - timedelta(minutes=6))]
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ) as hb:
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert "BTCUSDT" in result.get("missing_symbols", [])
    _args, kwargs = hb.call_args
    assert kwargs["status"] == "degraded"
    assert "BTCUSDT" in kwargs["details"]["missing_symbols"]


@pytest.mark.asyncio
async def test_just_opened_position_within_grace_does_not_trigger() -> None:
    """Open position held only 2 min — within 5-min grace. No flag."""
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [_position("BTCUSDT", _NOW - timedelta(minutes=2))]
    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ):
        result = await run_one_freshness_check(
            session_factory=sf, settings=_settings(),
            now_fn=lambda: _NOW,
        )
    assert "BTCUSDT" not in result.get("missing_symbols", [])


@pytest.mark.asyncio
async def test_3_triggers_in_10min_emits_resubscribe_metric() -> None:
    """Three missing-events within 10 min for same symbol → resubscribe metric."""
    from app.workers import ui_freshness_monitor as mod
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    # Reset process-local counter so this test is hermetic.
    mod._missing_event_counts.clear()

    sf = _session_factory()
    open_pos = [_position("BTCUSDT", _NOW - timedelta(hours=1))]

    ticks = [
        _NOW - timedelta(minutes=8),
        _NOW - timedelta(minutes=4),
        _NOW,
    ]

    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ), patch.object(mod.log, "warning") as log_warn:
        for tick in ticks:
            await run_one_freshness_check(
                session_factory=sf, settings=_settings(),
                now_fn=lambda t=tick: t,
            )

    # The resubscribe-request log line is the metric.
    # Look for the specific marker string.
    resub_calls = [
        c for c in log_warn.call_args_list
        if any(
            "healer_resubscribe_requested" in str(a)
            for a in c.args
        )
    ]
    assert len(resub_calls) >= 1, (
        f"expected resubscribe log, got: {log_warn.call_args_list!r}"
    )


@pytest.mark.asyncio
async def test_single_trigger_does_not_emit_resubscribe() -> None:
    """One missing event is not enough — needs ≥3 in 10 min."""
    from app.workers import ui_freshness_monitor as mod
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    mod._missing_event_counts.clear()

    sf = _session_factory()
    open_pos = [_position("BTCUSDT", _NOW - timedelta(hours=1))]

    with patch(
        "app.workers.ui_freshness_monitor.list_open_positions",
        new=AsyncMock(return_value=open_pos),
    ), patch(
        "app.workers.ui_freshness_monitor.get_last_pnl_tick_at",
        return_value={},
    ), patch(
        "app.workers.ui_freshness_monitor.record_heartbeat",
        new=AsyncMock(return_value=None),
    ), patch.object(mod.log, "warning") as log_warn:
        await run_one_freshness_check(
            session_factory=sf, settings=_settings(),
            now_fn=lambda: _NOW,
        )

    resub_calls = [
        c for c in log_warn.call_args_list
        if any(
            "healer_resubscribe_requested" in str(a)
            for a in c.args
        )
    ]
    assert len(resub_calls) == 0


@pytest.mark.asyncio
async def test_stale_symbols_distinct_from_missing() -> None:
    """Stale (emitted but old) vs missing (never emitted) are tracked separately."""
    from app.workers.ui_freshness_monitor import run_one_freshness_check

    sf = _session_factory()
    open_pos = [
        _position("BTCUSDT", _NOW - timedelta(hours=2)),   # stale: emitted 2h ago
        _position("ETHUSDT", _NOW - timedelta(hours=1)),   # missing: never emitted
    ]
    last_emit = {"BTCUSDT": _NOW - timedelta(hours=2)}
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
            session_factory=sf, settings=_settings(threshold=1800),
            now_fn=lambda: _NOW,
        )
    assert "BTCUSDT" in result.get("stale_symbols", [])
    assert "BTCUSDT" not in result.get("missing_symbols", [])
    assert "ETHUSDT" in result.get("missing_symbols", [])
    assert "ETHUSDT" not in result.get("stale_symbols", [])
