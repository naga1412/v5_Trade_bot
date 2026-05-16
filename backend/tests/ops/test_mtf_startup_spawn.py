"""MTF worker factory lifecycle tests (Task 6.1 — brought in with Task 3.1).

Tests that the factory functions produce cancellable asyncio.Tasks and that
the prewarm task completes cleanly on an empty universe.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.scoring.mtf_confluence import (
    WORKER_NAME_PREWARM,
    WORKER_NAME_REFRESH,
    _KLINE_CACHE,
    start_mtf_cache_prewarm_task,
    start_mtf_cache_ttl_refresh_task,
)


def _make_session_factory() -> MagicMock:
    """Return a minimal mock session_factory compatible with async context manager."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)
    return factory


@pytest.mark.asyncio
async def test_prewarm_task_completes_cleanly_on_empty_universe() -> None:
    """Empty universe → prewarm returns 0 entries; task completes (no hang)."""
    _KLINE_CACHE.clear()
    session_factory = _make_session_factory()

    # Patch load_current_universe to return an empty list
    with patch(
        "app.shadow.universe.load_current_universe",
        new_callable=AsyncMock,
        return_value=[],
    ):
        task = start_mtf_cache_prewarm_task(session_factory)
        assert task.get_name() == WORKER_NAME_PREWARM

        # Should complete within a short timeout (empty universe = no fetches)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("prewarm_task did not complete within 5s on empty universe")

    assert task.done()
    assert not task.cancelled()
    # No exception should have propagated
    exc = task.exception()
    assert exc is None


@pytest.mark.asyncio
async def test_refresh_task_cancellable() -> None:
    """Long-running refresh task can be cancelled cleanly."""
    session_factory = _make_session_factory()

    # Patch record_heartbeat so it doesn't try to use a real DB
    with patch(
        "app.ops.heartbeat.record_heartbeat",
        new_callable=AsyncMock,
        return_value=None,
    ):
        task = start_mtf_cache_ttl_refresh_task(session_factory)
        assert task.get_name() == WORKER_NAME_REFRESH
        assert not task.done()

        # Give the task a moment to start (it hits heartbeat then sleep)
        await asyncio.sleep(0.05)

        # Cancel and confirm clean teardown
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert task.done()
