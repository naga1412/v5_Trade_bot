"""Regression test for the 2026-08-05 root-cause: predictions.effective_score
and predictions.realized_vol_20d went 100% NULL from 2026-07-13 onward.

compute_realized_vol_20d (app/core/scoring/vol_normalization.py) needs
MIN_DAILY_BARS_FOR_VOL (20) distinct calendar days of 1h bars before it
computes anything instead of returning None. run_live_prediction's REST
seed used limit=300 (12.5 days at 1h) — every backend restart reset the
in-memory buffer below that floor, and needed ~7.5 more days of
*uninterrupted* uptime to cross it. Deploy cadence rose sharply starting
2026-07-14, so no restart since then had that much runway.

These tests pin two things so the fix can't silently regress:
1. HISTORY_SEED_BARS stays comfortably above the 20-calendar-day floor
   at the 1h timeframe this worker always runs at.
2. run_live_prediction actually requests HISTORY_SEED_BARS bars from
   the REST seed call, not some other hardcoded number.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.scoring.vol_normalization import MIN_DAILY_BARS_FOR_VOL
from app.data.adapters._base import Candle
from app.ws import live_prediction


def test_history_seed_bars_covers_the_realized_vol_floor_with_margin() -> None:
    """HISTORY_SEED_BARS (1h bars) must cover > MIN_DAILY_BARS_FOR_VOL days.

    Strictly greater than, not >=, so effective_score/realized_vol_20d
    populate starting with the very first candle after a restart instead
    of needing the process to also survive live past the exact floor.
    """
    days_covered = live_prediction.HISTORY_SEED_BARS / 24
    assert days_covered > MIN_DAILY_BARS_FOR_VOL


class _EmptyKlineStream:
    """Fake BinanceKlineStream whose stream() yields nothing.

    Lets run_live_prediction execute its REST-seed step and then return
    immediately (the `async for` loop over an empty generator exits at
    once) instead of blocking forever on a real WS connection.
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def stream(self):  # noqa: ANN201 — matches AsyncIterator[Candle] shape
        return
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_run_live_prediction_seeds_history_with_history_seed_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fetch_klines = AsyncMock(
        return_value=[
            Candle(
                ts=datetime(2026, 8, 5, tzinfo=timezone.utc),
                open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0,
            ),
        ],
    )

    class _FakeBinanceClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        fetch_klines = fake_fetch_klines

    monkeypatch.setattr(live_prediction, "BinanceClient", _FakeBinanceClient)
    monkeypatch.setattr(live_prediction, "BinanceKlineStream", _EmptyKlineStream)

    await live_prediction.run_live_prediction(symbol_pair="BTC/USDT", timeframe="1h")

    fake_fetch_klines.assert_awaited_once_with(
        "BTCUSDT", "1h", limit=live_prediction.HISTORY_SEED_BARS,
    )
