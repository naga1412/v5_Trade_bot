"""Phase 4 Task 5c -- universe_refresh_scheduler: cycle/heartbeat wiring
around refresh_live_fleet_universe.

The liquidity-floor selector logic itself (hysteresis, cold-start
seeding, open-position override) is already covered end-to-end by
tests/unit/test_live_fleet_universe.py -- these tests focus purely on
the scheduling layer this module adds: does one cycle call
refresh_live_fleet_universe and heartbeat 'ok', does an exception
heartbeat 'error' without propagating or killing the loop, and does the
forever-loop fire cycle 1 immediately (no sleep before it), mirroring
tests/workers/test_symbol_allowlist_refresh.py's own shape/assertions
for the equivalent cases.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.shadow.live_fleet_universe import LiveFleetEntry
from app.shadow.universe_refresh_scheduler import (
    WORKER_NAME,
    run_live_fleet_universe_refresh_loop,
    run_one_universe_refresh_cycle,
)


@pytest.fixture
async def session_factory():
    """Unused by the code under test in these scheduler-layer tests --
    both refresh_live_fleet_universe and record_heartbeat are mocked --
    but constructed for real (rather than a bare sentinel) so the fixture
    stays valid if a future test exercises it directly."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _http_and_rate_client() -> tuple[httpx.AsyncClient, object]:
    """Placeholders -- refresh_live_fleet_universe is mocked in every test
    below, so these are never actually dereferenced; only their identity
    matters (asserting the mock was called with them)."""
    return httpx.AsyncClient(), object()


@pytest.mark.asyncio
async def test_cycle_calls_refresh_and_heartbeats_ok(session_factory) -> None:
    http, rate_client = _http_and_rate_client()
    fake_entries = [
        LiveFleetEntry(symbol="BTCUSDT", cohort="established_top20",
                        qvol_24h=1e9, spread_bps=1.0, depth_0_5pct_usdt=1e6),
        LiveFleetEntry(symbol="NEWUSDT", cohort="liquidity_added_spot",
                        qvol_24h=2.5e7, spread_bps=3.0, depth_0_5pct_usdt=6e4),
    ]
    refresh_mock = AsyncMock(return_value=fake_entries)
    hb_mock = AsyncMock(return_value=None)

    with patch("app.shadow.universe_refresh_scheduler.refresh_live_fleet_universe", new=refresh_mock), \
         patch("app.shadow.universe_refresh_scheduler.record_heartbeat", new=hb_mock):
        count = await run_one_universe_refresh_cycle(
            session_factory=session_factory, http=http, rate_client=rate_client,
        )

    assert count == 2
    refresh_mock.assert_awaited_once_with(session_factory, http, rate_client)
    # Task 5e (2026-08-19): record_heartbeat is now awaited TWICE per cycle
    # (start-of-cycle + completion, see test_cycle_heartbeats_twice_start_
    # then_completion below for the dedicated, order-sensitive test of both
    # calls) -- this test still isolates the completion call specifically,
    # which is the last (most recent) call recorded on the mock.
    assert hb_mock.await_count == 2
    args, kwargs = hb_mock.call_args
    assert args[1] == WORKER_NAME
    assert kwargs.get("status") == "ok"
    assert kwargs["details"]["entries"] == 2
    assert kwargs["details"]["cohort_counts"] == {
        "established_top20": 1, "liquidity_added_spot": 1,
    }


@pytest.mark.asyncio
async def test_cycle_exception_heartbeats_error_and_does_not_propagate(session_factory) -> None:
    http, rate_client = _http_and_rate_client()
    refresh_mock = AsyncMock(side_effect=RuntimeError("binance exchangeInfo 500"))
    hb_mock = AsyncMock(return_value=None)

    with patch("app.shadow.universe_refresh_scheduler.refresh_live_fleet_universe", new=refresh_mock), \
         patch("app.shadow.universe_refresh_scheduler.record_heartbeat", new=hb_mock):
        # Must NOT raise -- the outer loop depends on this never propagating.
        count = await run_one_universe_refresh_cycle(
            session_factory=session_factory, http=http, rate_client=rate_client,
        )

    assert count == 0
    # Task 5e (2026-08-19): the start-of-cycle heartbeat still fires (status=
    # 'ok') even though the cycle then fails -- record_heartbeat is awaited
    # twice total (start, then the error completion this test isolates via
    # the last/most-recent call).
    assert hb_mock.await_count == 2
    args, kwargs = hb_mock.call_args
    assert args[1] == WORKER_NAME
    assert kwargs.get("status") == "error"
    assert "binance exchangeInfo 500" in kwargs["details"]["error"]


@pytest.mark.asyncio
async def test_loop_fires_cycle_immediately_then_sleeps(session_factory) -> None:
    """Cycle-before-sleep ordering, mirroring
    test_loop_first_cycle_completes_with_ok_heartbeat in
    tests/workers/test_symbol_allowlist_refresh.py: the previous
    sleep-first shape (seen elsewhere in this codebase, e.g. the
    pre-PR#344 symbol_allowlist_refresh loop) delayed every restart's
    first heartbeat by a full poll_interval_s. This asserts the fix's
    shape directly: fire cycle 1, THEN sleep."""
    http, rate_client = _http_and_rate_client()
    fake_entries = [
        LiveFleetEntry(symbol="BTCUSDT", cohort="established_top20",
                        qvol_24h=1e9, spread_bps=1.0, depth_0_5pct_usdt=1e6),
    ]
    refresh_mock = AsyncMock(return_value=fake_entries)
    hb_mock = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def fake_sleep(_: float) -> None:
        call_order.append("sleep")
        raise asyncio.CancelledError

    with patch("app.shadow.universe_refresh_scheduler.refresh_live_fleet_universe", new=refresh_mock), \
         patch("app.shadow.universe_refresh_scheduler.record_heartbeat", new=hb_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_live_fleet_universe_refresh_loop(
                session_factory=session_factory, http=http, rate_client=rate_client,
                poll_interval_s=6 * 60 * 60, _sleep=fake_sleep,
            )

    # The cycle ran to completion (start heartbeat + refresh + completion
    # heartbeat) BEFORE the first (and only, since it cancels) sleep call.
    assert call_order == ["sleep"]
    refresh_mock.assert_awaited_once()
    # Task 5e (2026-08-19): record_heartbeat is awaited twice per cycle now
    # (start-of-cycle + completion) -- the most recent call is still the
    # completion heartbeat this assertion checks.
    assert hb_mock.await_count == 2
    assert hb_mock.call_args.kwargs.get("status") == "ok"


@pytest.mark.asyncio
async def test_cycle_heartbeats_twice_start_then_completion(session_factory) -> None:
    """Task 5e (ratified 2026-08-19): a bare completion-only heartbeat
    leaves live_fleet_universe_refresh_task heartbeat-invisible for the
    entire sweep duration (up to ~50-60 minutes even after Task 5e's own
    cold-start speedup, for every warm 6-hourly refresh) -- the same
    silent-worker shape as the symbol_allowlist_refresh sleep-first bug
    from July. This asserts record_heartbeat is called TWICE during one
    cycle: once at the start (status='ok', details={'sweep':
    'in_progress'}), once at completion (the existing entries/
    cohort_counts heartbeat, unchanged) -- and that the start call
    happens strictly BEFORE refresh_live_fleet_universe is awaited, via
    a shared call_order list both mocks append to."""
    http, rate_client = _http_and_rate_client()
    fake_entries = [
        LiveFleetEntry(symbol="BTCUSDT", cohort="established_top20",
                        qvol_24h=1e9, spread_bps=1.0, depth_0_5pct_usdt=1e6),
    ]
    call_order: list[str] = []

    async def fake_refresh(*_args, **_kwargs):
        call_order.append("refresh")
        return fake_entries

    async def fake_heartbeat(*_args, **kwargs):
        call_order.append(f"heartbeat:{kwargs.get('status')}")

    refresh_mock = AsyncMock(side_effect=fake_refresh)
    hb_mock = AsyncMock(side_effect=fake_heartbeat)

    with patch("app.shadow.universe_refresh_scheduler.refresh_live_fleet_universe", new=refresh_mock), \
         patch("app.shadow.universe_refresh_scheduler.record_heartbeat", new=hb_mock):
        count = await run_one_universe_refresh_cycle(
            session_factory=session_factory, http=http, rate_client=rate_client,
        )

    assert count == 1
    assert hb_mock.await_count == 2
    assert call_order == ["heartbeat:ok", "refresh", "heartbeat:ok"]

    first_call, second_call = hb_mock.call_args_list
    assert first_call.args[1] == WORKER_NAME
    assert first_call.kwargs.get("status") == "ok"
    assert first_call.kwargs["details"] == {"sweep": "in_progress"}

    assert second_call.args[1] == WORKER_NAME
    assert second_call.kwargs.get("status") == "ok"
    assert second_call.kwargs["details"]["entries"] == 1
    assert second_call.kwargs["details"]["cohort_counts"] == {"established_top20": 1}


@pytest.mark.asyncio
async def test_loop_survives_cycle_exception_and_still_sleeps(session_factory) -> None:
    """A cycle that errors must not kill the outer loop -- it heartbeats
    'error' (proven above at the cycle level) and the loop proceeds to
    sleep for the next attempt rather than propagating the exception."""
    http, rate_client = _http_and_rate_client()
    refresh_mock = AsyncMock(side_effect=RuntimeError("transient"))
    hb_mock = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def fake_sleep(_: float) -> None:
        call_order.append("sleep")
        raise asyncio.CancelledError

    with patch("app.shadow.universe_refresh_scheduler.refresh_live_fleet_universe", new=refresh_mock), \
         patch("app.shadow.universe_refresh_scheduler.record_heartbeat", new=hb_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_live_fleet_universe_refresh_loop(
                session_factory=session_factory, http=http, rate_client=rate_client,
                poll_interval_s=6 * 60 * 60, _sleep=fake_sleep,
            )

    assert call_order == ["sleep"]
    assert hb_mock.call_args.kwargs.get("status") == "error"
