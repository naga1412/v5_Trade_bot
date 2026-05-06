import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.news import ingest_worker
from app.ops import pause_state


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_news_ingest_loop_skips_iteration_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    iter_spy = AsyncMock()
    monkeypatch.setattr(ingest_worker, "_run_one_iteration", iter_spy)
    monkeypatch.setattr(
        ingest_worker, "_build_adapters", lambda *_a, **_k: (object(), object()),
    )

    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_ingest_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )

    iter_spy.assert_not_awaited()
    assert sleeps[0] == float(ingest_worker.CRYPTO_INTERVAL_S)


@pytest.mark.asyncio
async def test_news_ingest_loop_runs_iteration_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iter_spy = AsyncMock()
    monkeypatch.setattr(ingest_worker, "_run_one_iteration", iter_spy)
    monkeypatch.setattr(
        ingest_worker, "_build_adapters", lambda *_a, **_k: (object(), object()),
    )

    async def _sleep(_s: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_ingest_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )
    iter_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_news_cleanup_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    monkeypatch.setattr(
        ingest_worker, "_seconds_until_next_utc_hour", lambda *a, **k: 0.0,
    )
    cleanup_spy = AsyncMock()
    # The lazy import inside the loop resolves through this attribute.
    import app.news.persistence as persistence
    monkeypatch.setattr(persistence, "cleanup_old_news", cleanup_spy)

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ingest_worker.run_news_cleanup_loop(
            session_factory=lambda: None, sleep_fn=_sleep,
        )
    cleanup_spy.assert_not_awaited()
