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
async def test_is_paused_defaults_false() -> None:
    assert await pause_state.is_paused() is False


@pytest.mark.asyncio
async def test_set_paused_true_then_is_paused_true() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="travel", session=sess,
    )
    pause_state._CACHE = None  # force re-read from Redis
    assert await pause_state.is_paused() is True


@pytest.mark.asyncio
async def test_set_paused_false_clears_state() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(True, by_email="a@x.com", reason="r", session=sess)
    await pause_state.set_paused(False, by_email="a@x.com", reason=None, session=sess)
    pause_state._CACHE = None
    assert await pause_state.is_paused() is False
    state = await pause_state.get_state()
    assert state.paused is False
    assert state.since is None
    assert state.by_email is None


@pytest.mark.asyncio
async def test_get_state_returns_full_record_when_paused() -> None:
    sess = MagicMock()
    sess.execute = AsyncMock()
    sess.commit = AsyncMock()
    await pause_state.set_paused(
        True, by_email="admin@x.com", reason="broker outage", session=sess,
    )
    state = await pause_state.get_state()
    assert state.paused is True
    assert state.by_email == "admin@x.com"
    assert state.reason == "broker outage"
    assert state.since is not None
