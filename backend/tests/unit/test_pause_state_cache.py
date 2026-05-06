from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.ops import pause_state


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_cache_avoids_redis_within_1s(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    pause_state._CACHE = None
    # First call populates the cache with True.
    assert await pause_state.is_paused() is True

    # Now flip Redis directly - within 1s the cache should still answer True.
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "false")
    t = [100.0]
    monkeypatch.setattr(pause_state.time, "monotonic", lambda: t[0])
    pause_state._CACHE = (True, t[0] - 0.5)  # cache age 0.5s
    assert await pause_state.is_paused() is True


@pytest.mark.asyncio
async def test_cache_expires_after_1s(monkeypatch: pytest.MonkeyPatch) -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "false")
    t = [100.0]
    monkeypatch.setattr(pause_state.time, "monotonic", lambda: t[0])
    pause_state._CACHE = (True, t[0] - 1.5)  # cache age 1.5s - expired
    assert await pause_state.is_paused() is False


@pytest.mark.asyncio
async def test_set_paused_invalidates_cache() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    pause_state._CACHE = (False, 100.0)
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    assert pause_state._CACHE is None
