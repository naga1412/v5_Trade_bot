import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pandas as pd
import pytest

from app.ops import pause_state, verifier_scheduler
from app.shadow.worker import ShadowWorker


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    pause_state._reset_for_tests()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(pause_state, "_get_redis", lambda: fake)
    yield fake
    pause_state._reset_for_tests()


@pytest.mark.asyncio
async def test_shadow_worker_skips_candle_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    w = ShadowWorker(
        symbols=["BTCUSDT"],
        session_factory=MagicMock(),
        reader=MagicMock(),
        seed_history={"BTCUSDT": pd.DataFrame()},
    )
    open_spy = AsyncMock()
    close_spy = AsyncMock()
    monkeypatch.setattr(w, "_maybe_open_position", open_spy)
    monkeypatch.setattr(w, "_maybe_close_position", close_spy)

    candle = MagicMock(symbol="BTCUSDT", timeframe="1h",
                      ts=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
                      open=1, high=1, low=1, close=1, volume=1)

    await w._handle_candle(candle)
    open_spy.assert_not_awaited()
    close_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_verifier_loop_skips_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = pause_state._get_redis()
    await fake.set(pause_state.REDIS_KEY, "true")

    check_spy = AsyncMock()
    monkeypatch.setattr(verifier_scheduler, "_check_all_chains", check_spy)
    monkeypatch.setattr(
        verifier_scheduler, "seconds_until_next_utc_hour", lambda *a, **k: 0,
    )

    sleeps = 0
    async def _sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await verifier_scheduler.run_audit_verifier_loop(
            session_factory=MagicMock(),
            _sleep=_sleep,
            _now=lambda: datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
        )
    check_spy.assert_not_awaited()
