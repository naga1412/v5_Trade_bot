import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.data import intermarket_worker
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_intermarket_snapshot_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    snapshot_spy = AsyncMock(return_value=0)
    monkeypatch.setattr(intermarket_worker, "_snapshot_once", snapshot_spy)

    sleeps: list[float] = []
    async def _sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await intermarket_worker.run_intermarket_snapshot_loop(
            session_factory=lambda: None,
            _adapter=object(),
            _sleep=_sleep,
            _universe_loader=AsyncMock(return_value=[]),
        )
    snapshot_spy.assert_not_awaited()
    assert sleeps[0] == float(intermarket_worker.INTERMARKET_INTERVAL_S)


@pytest.mark.asyncio
async def test_intermarket_cleanup_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    monkeypatch.setattr(
        intermarket_worker, "_seconds_until_0430_utc", lambda **_k: 0,
    )
    cleanup_spy = AsyncMock()
    monkeypatch.setattr(
        intermarket_worker, "cleanup_old_intermarket", cleanup_spy,
    )

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await intermarket_worker.run_intermarket_cleanup_loop(
            session_factory=lambda: None, _sleep=_sleep,
        )
    cleanup_spy.assert_not_awaited()
