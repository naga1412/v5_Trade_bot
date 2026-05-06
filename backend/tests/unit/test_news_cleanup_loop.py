"""Unit tests for SP-9 Phase D3 nightly news cleanup loop."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

import app.news.ingest_worker as iw


# ---------------------------------------------------------------------------
# _seconds_until_next_utc_hour math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "hour", "expected"),
    [
        # Now=01:30 UTC, target=04:00 UTC → 2.5h.
        (datetime(2026, 5, 6, 1, 30, tzinfo=timezone.utc), 4, 2.5 * 3600),
        # Now=04:00 sharp, target=04:00 → next day, 24h.
        (datetime(2026, 5, 6, 4, 0, 0, tzinfo=timezone.utc), 4, 24 * 3600),
        # Now=12:00 UTC, target=04:00 UTC → 16h to next 04:00.
        (datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc), 4, 16 * 3600),
        # Now=03:59:30, target=04:00 → 30 seconds.
        (datetime(2026, 5, 6, 3, 59, 30, tzinfo=timezone.utc), 4, 30),
    ],
)
def test_seconds_until_next_utc_hour_parametric(
    now: datetime, hour: int, expected: float,
) -> None:
    assert iw._seconds_until_next_utc_hour(hour, now=now) == expected


def test_seconds_until_next_utc_hour_handles_naive_datetime() -> None:
    naive = datetime(2026, 5, 6, 1, 30)  # interpreted as UTC
    assert iw._seconds_until_next_utc_hour(4, now=naive) == 2.5 * 3600


# ---------------------------------------------------------------------------
# run_news_cleanup_loop driver
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _factory(captured: list[_FakeSession]) -> Any:
    @asynccontextmanager
    async def _ctx() -> AsyncIterator[_FakeSession]:
        s = _FakeSession()
        captured.append(s)
        yield s

    def _make() -> Any:
        return _ctx()

    return _make


@pytest.mark.asyncio
async def test_cleanup_loop_calls_cleanup_old_news_with_20day_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[dict[str, Any]] = []

    async def _fake_cleanup(session: Any, *, older_than_days: int = 20) -> int:
        cleanup_calls.append({"older_than_days": older_than_days})
        return 7  # number of rows deleted

    monkeypatch.setattr(
        "app.news.persistence.cleanup_old_news", _fake_cleanup,
    )

    sleep_calls: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:  # break out after 1 cleanup round
            raise asyncio.CancelledError()

    captured: list[_FakeSession] = []
    factory = _factory(captured)

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_cleanup_loop(factory, sleep_fn=_sleep)

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["older_than_days"] == 20
    # The session opened by the loop committed exactly once.
    assert len(captured) == 1
    assert captured[0].commits == 1


@pytest.mark.asyncio
async def test_cleanup_loop_swallows_exception_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash inside cleanup_old_news must not break the loop."""
    call_count = 0

    async def _flaky_cleanup(session: Any, *, older_than_days: int = 20) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("db blew up")
        return 0

    monkeypatch.setattr(
        "app.news.persistence.cleanup_old_news", _flaky_cleanup,
    )

    sleep_calls = 0

    async def _sleep(s: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 3:
            raise asyncio.CancelledError()

    factory = _factory([])

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_cleanup_loop(factory, sleep_fn=_sleep)

    # 3 sleeps → 3 iterations attempted.
    assert call_count >= 2


@pytest.mark.asyncio
async def test_cleanup_loop_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _cancelling_cleanup(session: Any, *, older_than_days: int = 20) -> int:
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "app.news.persistence.cleanup_old_news", _cancelling_cleanup,
    )

    async def _sleep(_s: float) -> None:
        return None

    factory = _factory([])

    with pytest.raises(asyncio.CancelledError):
        await iw.run_news_cleanup_loop(factory, sleep_fn=_sleep)
