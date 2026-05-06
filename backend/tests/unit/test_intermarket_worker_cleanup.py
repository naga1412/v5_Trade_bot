import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.intermarket_worker import (
    _seconds_until_0430_utc,
    run_intermarket_cleanup_loop,
)


def _fake_factory():
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=None)
    return sf


def test_seconds_until_0430_at_0400_returns_30min() -> None:
    n = datetime(2026, 5, 6, 4, 0, tzinfo=timezone.utc)
    s = _seconds_until_0430_utc(now=n)
    assert s == 30 * 60


def test_seconds_until_0430_at_0430_exactly_returns_24h() -> None:
    n = datetime(2026, 5, 6, 4, 30, tzinfo=timezone.utc)
    s = _seconds_until_0430_utc(now=n)
    assert s == 24 * 60 * 60


def test_seconds_until_0430_at_0500_wraps_to_next_day() -> None:
    n = datetime(2026, 5, 6, 5, 0, tzinfo=timezone.utc)
    s = _seconds_until_0430_utc(now=n)
    assert s == (24 - 1) * 60 * 60 + 30 * 60  # 23h30m


@pytest.mark.asyncio
async def test_cleanup_loop_invokes_cleanup_then_cancels() -> None:
    sleep_log: list[float] = []
    async def fake_sleep(s: float) -> None:
        sleep_log.append(s)
        # Loop runs as: sleep → cleanup → sleep → cleanup ...
        # Cancel on the SECOND sleep so cleanup runs exactly once first.
        if len(sleep_log) >= 2:
            raise asyncio.CancelledError

    def fixed_now() -> datetime:
        return datetime(2026, 5, 6, 4, 0, tzinfo=timezone.utc)
    with patch("app.data.intermarket_worker.cleanup_old_intermarket",
               new=AsyncMock(return_value=42)) as cleanup:
        with pytest.raises(asyncio.CancelledError):
            await run_intermarket_cleanup_loop(
                _fake_factory(), _sleep=fake_sleep, _now=fixed_now,
            )
    cleanup.assert_awaited_once()
    assert sleep_log[0] == 30 * 60.0


@pytest.mark.asyncio
async def test_cleanup_loop_swallows_errors() -> None:
    sleep_count = {"n": 0}
    async def fake_sleep(s: float) -> None:
        sleep_count["n"] += 1
        # Cancel on the second sleep so cleanup runs once and raises first.
        if sleep_count["n"] >= 2:
            raise asyncio.CancelledError

    def fixed_now() -> datetime:
        return datetime(2026, 5, 6, 12, tzinfo=timezone.utc)
    with patch("app.data.intermarket_worker.cleanup_old_intermarket",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(asyncio.CancelledError):
            await run_intermarket_cleanup_loop(
                _fake_factory(), _sleep=fake_sleep, _now=fixed_now,
            )
