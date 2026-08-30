"""Item 0 (2026-08-30) -- futures_only_refresh: cycle/heartbeat wiring
around cohort_cache.refresh_futures_only_cache.

Mirrors test_universe_refresh_scheduler.py's shape/assertions for the
equivalent cases (that module's own docstring explains the origin of
this pattern: tests/workers/test_symbol_allowlist_refresh.py).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workers.futures_only_refresh import (
    WORKER_NAME,
    run_futures_only_refresh_loop,
    run_one_futures_only_refresh_cycle,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_cycle_success_heartbeats_ok(session_factory) -> None:
    http = httpx.AsyncClient()
    refresh_mock = AsyncMock(return_value={"XPLUSDT", "KITEUSDT"})
    hb_mock = AsyncMock(return_value=None)

    with patch("app.workers.futures_only_refresh.refresh_futures_only_cache", new=refresh_mock), \
         patch("app.workers.futures_only_refresh.record_heartbeat", new=hb_mock):
        count = await run_one_futures_only_refresh_cycle(
            session_factory=session_factory, http=http,
        )

    assert count == 2
    refresh_mock.assert_awaited_once_with(http)
    hb_mock.assert_awaited_once()
    args, kwargs = hb_mock.call_args
    assert args[1] == WORKER_NAME
    assert kwargs.get("status") == "ok"
    assert kwargs["details"]["futures_only_count"] == 2


@pytest.mark.asyncio
async def test_cycle_failure_heartbeats_error_and_does_not_propagate(session_factory) -> None:
    """refresh_futures_only_cache returns None on failure (its own
    contract, see test_cohort_cache.py) -- this must heartbeat 'error',
    not raise, and must not report a positive count."""
    http = httpx.AsyncClient()
    refresh_mock = AsyncMock(return_value=None)
    hb_mock = AsyncMock(return_value=None)

    with patch("app.workers.futures_only_refresh.refresh_futures_only_cache", new=refresh_mock), \
         patch("app.workers.futures_only_refresh.record_heartbeat", new=hb_mock):
        count = await run_one_futures_only_refresh_cycle(
            session_factory=session_factory, http=http,
        )

    assert count == 0
    hb_mock.assert_awaited_once()
    args, kwargs = hb_mock.call_args
    assert args[1] == WORKER_NAME
    assert kwargs.get("status") == "error"


@pytest.mark.asyncio
async def test_loop_fires_cycle_immediately_then_sleeps(session_factory) -> None:
    http = httpx.AsyncClient()
    refresh_mock = AsyncMock(return_value={"XPLUSDT"})
    hb_mock = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def fake_sleep(_: float) -> None:
        call_order.append("sleep")
        raise asyncio.CancelledError

    with patch("app.workers.futures_only_refresh.refresh_futures_only_cache", new=refresh_mock), \
         patch("app.workers.futures_only_refresh.record_heartbeat", new=hb_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_futures_only_refresh_loop(
                session_factory=session_factory, http=http,
                poll_interval_s=86400.0, _sleep=fake_sleep,
            )

    refresh_mock.assert_awaited_once()
    assert call_order == ["sleep"]


@pytest.mark.asyncio
async def test_loop_survives_cycle_exception_and_still_sleeps(session_factory) -> None:
    http = httpx.AsyncClient()
    refresh_mock = AsyncMock(side_effect=RuntimeError("transient"))
    hb_mock = AsyncMock(return_value=None)

    call_order: list[str] = []

    async def fake_sleep(_: float) -> None:
        call_order.append("sleep")
        raise asyncio.CancelledError

    with patch("app.workers.futures_only_refresh.refresh_futures_only_cache", new=refresh_mock), \
         patch("app.workers.futures_only_refresh.record_heartbeat", new=hb_mock):
        with pytest.raises(asyncio.CancelledError):
            await run_futures_only_refresh_loop(
                session_factory=session_factory, http=http,
                poll_interval_s=86400.0, _sleep=fake_sleep,
            )

    assert call_order == ["sleep"]
