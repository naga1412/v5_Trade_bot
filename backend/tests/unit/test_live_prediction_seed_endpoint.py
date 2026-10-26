"""#539 regression, re-asserted at the ENDPOINT rather than at a class name.

The original regression test (``test_live_prediction_candle_source.py``)
asserted that the SPOT adapter class was never *constructed* for a
``futures_poll`` symbol, and that a specific futures helper *was* called.
Both are proxies for the thing that actually matters -- which URL the seed
requests -- and both stopped testing anything the moment FU-53 routed the
seed through ``app.data.bar_fetch`` instead.

That is the weakness worth naming: a test coupled to *how* the right answer
is produced goes quiet when the how changes, while still passing. These
tests assert the URL the request was made to, so they survive any
refactor that keeps the behaviour and fail on any that breaks it.

WHY THE ENDPOINT IS THE RIGHT ASSERTION: a ``futures_poll`` symbol has no
spot pair, so ``api.binance.com/api/v3/klines`` returns an empty payload
for it on every call, forever. #539 is what that costs -- the entire
cohort produced zero predictions for its whole existence, because an empty
list reads as "no data yet" rather than as an error. See
``docs/superpowers/decisions/2026-09-01-futures-poll-seed-was-spot-only.md``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.data.bar_fetch import FUTURES_KLINES_URL, SPOT_KLINES_URL
from app.ws import live_prediction


class _Resp:
    """Minimal httpx-shaped response carrying one kline row."""

    def __init__(self) -> None:
        # [open_time_ms, open, high, low, close, volume, ...] -- the real
        # 12-element shape, trimmed to the six fields the parser reads.
        self._rows = [[1767225600000, "1.0", "1.0", "1.0", "1.0", "1.0"]]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[list[str | int]]:
        return self._rows


class _RecordingRateClient:
    """Captures the URL the seed requested, and nothing else.

    Stands in for the app's shared ``RateLimitedClient``. Recording the URL
    is the whole point: it is the one observable that distinguishes a spot
    fetch from a futures fetch.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def request(self, _method: str, url: str, **_: Any) -> _Resp:
        self.urls.append(url)
        return _Resp()


class _EmptyKlineStream:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def stream(self) -> Any:
        return
        yield  # pragma: no cover - makes this an async generator


async def _seed_url_for(symbol_source: str | None, symbol_pair: str) -> list[str]:
    """Run one seed and return the URLs it requested."""
    recorder = _RecordingRateClient()
    adapter = type("FakeAdapter", (), {"rate_client": recorder})()
    kwargs: dict[str, Any] = {"symbol_pair": symbol_pair, "timeframe": "1h"}
    if symbol_source is not None:
        kwargs["symbol_source"] = symbol_source
    with patch("app.data.adapters.get_intermarket_adapter", return_value=adapter), \
         patch.object(live_prediction, "BinanceKlineStream", _EmptyKlineStream):
        await live_prediction.run_live_prediction(**kwargs)
    return recorder.urls


@pytest.mark.asyncio
async def test_futures_only_cohort_seeds_from_the_futures_endpoint() -> None:
    """THE #539 ASSERTION. Not "a class was not constructed" -- the URL."""
    urls = await _seed_url_for("futures_poll", "ZORA/USDT")
    assert urls, "the seed made no request at all, so nothing was asserted"
    assert all(u == FUTURES_KLINES_URL for u in urls), (
        f"futures_poll seeded from {urls!r}. A futures-only symbol has no spot "
        f"pair, so anything but {FUTURES_KLINES_URL} returns an empty payload "
        "forever and reads like 'no data yet' -- that is #539, which cost the "
        "whole cohort every prediction it should have made."
    )


@pytest.mark.asyncio
async def test_spot_cohorts_still_seed_from_the_spot_endpoint() -> None:
    """The inverse, and it is not redundant.

    A seed hardwired to fapi passes the test above and is just as broken:
    ``established_top20`` is selected by SPOT volume and its members are not
    all listed as perpetuals. Only asserting both directions pins this to
    "chooses from the cohort" rather than "happens to be right once".
    """
    for cohort in (None, "established_top20", "liquidity_added_spot"):
        urls = await _seed_url_for(cohort, "BTC/USDT")
        assert urls, f"cohort {cohort!r}: the seed made no request"
        assert all(u == SPOT_KLINES_URL for u in urls), (
            f"cohort {cohort!r} seeded from {urls!r}, expected {SPOT_KLINES_URL}. "
            "Defaulting everything to futures is the mirror of #539."
        )


@pytest.mark.asyncio
async def test_the_seed_uses_the_apps_own_rate_client() -> None:
    """FU-40: the shared limiter, not a fresh one.

    ``RateLimitedClient`` holds the Binance weight budget as *instance*
    state, so a per-call client shares no budget with the app. That is how
    fire-and-forget flow-feature writes burst 90-138 calls an hour past the
    limiter. The recorder here IS the app's client, so a seed that opened
    its own would record nothing -- which is what the `assert urls` in the
    tests above actually detects.
    """
    recorder = _RecordingRateClient()
    adapter = type("FakeAdapter", (), {"rate_client": recorder})()
    with patch("app.data.adapters.get_intermarket_adapter", return_value=adapter), \
         patch.object(live_prediction, "BinanceKlineStream", _EmptyKlineStream):
        await live_prediction.run_live_prediction(
            symbol_pair="BTC/USDT", timeframe="1h",
        )
    assert len(recorder.urls) == 1, (
        f"the shared rate client saw {len(recorder.urls)} requests, expected 1. "
        "Zero means the seed opened its own HTTP client and bypassed the weight "
        "budget (FU-40)."
    )


@pytest.mark.asyncio
async def test_the_seed_requests_history_seed_bars() -> None:
    """The limit is carried through the new helper, not silently defaulted.

    ``HISTORY_SEED_BARS`` is 504 and was raised from 300 by PR #400 for a
    measured reason; a refactor that let the helper's own default (500)
    win would be a quiet 4-bar regression.
    """
    captured: dict[str, Any] = {}

    class _CapturingRateClient(_RecordingRateClient):
        async def request(self, _method: str, url: str, **kw: Any) -> _Resp:
            captured.update(kw.get("params") or {})
            return await super().request(_method, url, **kw)

    recorder = _CapturingRateClient()
    adapter = type("FakeAdapter", (), {"rate_client": recorder})()
    with patch("app.data.adapters.get_intermarket_adapter", return_value=adapter), \
         patch.object(live_prediction, "BinanceKlineStream", _EmptyKlineStream):
        await live_prediction.run_live_prediction(
            symbol_pair="BTC/USDT", timeframe="1h",
        )

    assert captured.get("symbol") == "BTCUSDT"
    assert captured.get("interval") == "1h"
    assert int(captured["limit"]) == live_prediction.HISTORY_SEED_BARS, (
        f"requested limit={captured.get('limit')!r}, expected "
        f"{live_prediction.HISTORY_SEED_BARS} (HISTORY_SEED_BARS). An exact "
        "equality, not >=: the helper's own default is 500, so an inequality "
        "would pass while silently losing the 504th bar PR #400 added."
    )
