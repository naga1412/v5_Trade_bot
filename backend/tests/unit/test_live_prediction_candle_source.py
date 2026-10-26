# backend/tests/unit/test_live_prediction_candle_source.py
"""Step 0 guard: the WS path must still construct and consume its own
BinanceKlineStream when no candle_source is injected — a regression here
means a future change silently repointed the default source.

NOTE on mocking strategy: the plan doc's original guard-test snippet
patches ``app.ws.live_prediction.httpx.AsyncClient`` broadly and lets the
real ``BinanceClient.fetch_klines`` -> ``RateLimitedClient.request`` path
run against it. That trips a pre-existing incompatibility, unrelated to
this task's refactor: ``RateLimitedClient``'s header-sync logic
(``app/data/ratelimit.py``, ``sync_header="X-MBX-USED-WEIGHT-1M"`` is
hardcoded/always-on in ``_default_rate_client``) calls
``response.headers.get(...)`` expecting a plain value, but a broadly
mocked async-context-manager chain resolves that to an unawaited
coroutine, raising ``TypeError: float() argument ... not 'coroutine'``.
Reproduced identically against the unmodified pre-refactor function, so
it is not caused by this change. This file instead mocks
``app.ws.live_prediction.BinanceClient`` directly (matching the working
pattern already used by the adjacent reference test
``test_live_prediction_history_seed.py``), which sidesteps the
rate-limiter entirely while still exercising the exact property under
test: does the WS path construct its own ``BinanceKlineStream`` by
default, and does an injected ``candle_source`` bypass it.

Same reasoning applies to ``manager``: bare ``patch("...manager")``
yields a plain (non-async) MagicMock, but the real code does
``await manager.publish(...)`` — patching the specific method with
``new_callable=AsyncMock`` instead keeps the mock awaitable without
changing what's being verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ws.live_prediction import run_live_prediction


class _SeedResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[list[str | int]]:
        return [[1767225600000, "1.0", "1.0", "1.0", "1.0", "1.0"]]


class _SeedingRateClient:
    """Intercepts the history seed at the seam FU-53 introduced.

    These tests are about CANDLE SOURCE, not the seed, so the seed only has
    to return one usable bar without touching the network. It used to be
    stubbed by patching ``live_prediction.BinanceClient``; FU-53 routes the
    seed through ``app.data.bar_fetch``, so that patch silently stopped
    intercepting anything and these tests began reaching the real HTTP
    path. Patching the app's own rate client is the seam that survives,
    because every seed goes through it (FU-40).
    """

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def request(self, _method: str, url: str, **_: Any) -> _SeedResp:
        self.urls.append(url)
        return _SeedResp()


def _seed_patch() -> Any:
    adapter = type("FakeAdapter", (), {"rate_client": _SeedingRateClient()})()
    return patch("app.data.adapters.get_intermarket_adapter", return_value=adapter)


@pytest.mark.asyncio
async def test_ws_path_constructs_own_stream_when_no_candle_source_given():
    with _seed_patch(), \
         patch("app.ws.live_prediction.BinanceKlineStream") as mock_stream_cls:
        mock_stream = mock_stream_cls.return_value

        async def empty_stream():
            return
            yield  # pragma: no cover — makes this an async generator

        mock_stream.stream = lambda: empty_stream()
        await run_live_prediction(symbol_pair="ETH/USDT", timeframe="1h")

    mock_stream_cls.assert_called_once_with(symbol="ETHUSDT", timeframe="1h")


@pytest.mark.asyncio
async def test_injected_candle_source_is_consumed_instead_of_ws():
    """When candle_source is supplied, BinanceKlineStream must never be
    constructed — proves the two paths are mutually exclusive, not that
    one silently falls back to the other."""
    from app.shadow.multi_stream import MultiStreamCandle

    candle = MultiStreamCandle(
        symbol="SOLUSDT", timeframe="1h",
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0,
    )

    async def one_candle():
        yield candle

    with _seed_patch(), \
         patch("app.ws.live_prediction.BinanceKlineStream") as mock_stream_cls, \
         patch("app.ws.live_prediction.build_prediction", new_callable=AsyncMock) as mock_build, \
         patch("app.ws.live_prediction._persist_prediction_and_schedule_validation", new_callable=AsyncMock), \
         patch("app.ws.live_prediction.record_heartbeat", new_callable=AsyncMock), \
         patch("app.ws.live_prediction._maybe_dispatch", new_callable=AsyncMock), \
         patch("app.ws.live_prediction.manager.publish", new_callable=AsyncMock):
        mock_pred = AsyncMock()
        mock_pred.layer_scores = {}
        mock_pred.prediction_extras = None
        mock_pred.symbol = "SOL/USDT"
        mock_pred.timeframe = "1h"
        mock_pred.final.direction = "NEUTRAL"
        mock_pred.final.score = 0.0
        mock_pred.final.confidence = 0.0
        mock_pred.ts = candle.ts
        mock_pred.mtf_agreement = None
        mock_pred.mtf_dominant_tf = None
        mock_pred.mtf_directions_json = None
        mock_pred.p_win = None
        mock_pred.effective_score = None
        mock_pred.realized_vol_20d = None
        mock_pred.funding_directional_adj = None
        mock_pred.funding_rate_daily = None
        mock_pred.model_dump = lambda mode=None: {}
        mock_build.return_value = mock_pred

        await run_live_prediction(
            symbol_pair="SOL/USDT", timeframe="1h", candle_source=one_candle(),
        )

    mock_stream_cls.assert_not_called()
    mock_build.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2026-09-01 fix: symbol_source="futures_poll" must seed from Binance
# FUTURES REST, never the default SPOT path — futures-only symbols have no
# spot pair by definition, so the SPOT seed used by every other caller is a
# guaranteed permanent failure for this cohort. Root-caused after it
# produced zero predictions, ever, since Stage 1's promotion. See
# docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md.


# ---------------------------------------------------------------------------
# THE TWO SEED-PATH TESTS THAT LIVED HERE ARE SUPERSEDED BY
# tests/unit/test_live_prediction_seed_endpoint.py (FU-53).
#
# They asserted the seed path by PROXY: that the spot adapter CLASS was not
# constructed, and that a named futures helper WAS called. FU-53 routes the
# seed through app.data.bar_fetch, so neither proxy observes the seed any
# more -- and the spot-direction one had become a check that CANNOT FAIL:
# it asserted `fetch_futures_seed_klines` was not called, and that helper is
# now called for no cohort at all, so it passed vacuously and would have
# kept passing had the spot cohort started seeding from fapi.
#
# The replacements assert the URL the request was actually made to, in both
# directions, which is strictly stronger and survives this kind of refactor:
#   test_futures_only_cohort_seeds_from_the_futures_endpoint
#   test_spot_cohorts_still_seed_from_the_spot_endpoint
# ---------------------------------------------------------------------------
