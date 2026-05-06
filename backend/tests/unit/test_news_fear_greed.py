"""Unit tests for SP-9 Phase D1 alternative.me Fear & Greed fetcher.

Covers:
* fresh fetch parses the JSON shape into ``FngResult``
* a second call within 1h hits the in-process cache (no HTTP call)
* once the TTL expires the next call refetches
* network errors propagate (don't poison the cache)
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

import app.news.fear_greed as fg
from app.news.fear_greed import FngResult, get_fear_greed_index


_SAMPLE_PAYLOAD = {
    "name": "Fear and Greed Index",
    "data": [
        {
            "value": "72",
            "value_classification": "Greed",
            "timestamp": "1746489600",  # 2025-05-06T00:00:00Z
            "time_until_update": "12345",
        }
    ],
    "metadata": {"error": None},
}


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with an empty module cache."""
    fg._cache = None
    yield
    fg._cache = None


@pytest.mark.asyncio
async def test_fresh_fetch_returns_parsed_fng_result() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(200, json=_SAMPLE_PAYLOAD),
        )
        result = await get_fear_greed_index()

    assert route.called
    assert isinstance(result, FngResult)
    assert result.value == 72
    assert result.label == "Greed"
    assert result.timestamp == datetime(2025, 5, 6, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_cached_fetch_within_ttl_skips_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First call populates cache.
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(200, json=_SAMPLE_PAYLOAD),
        )
        first = await get_fear_greed_index()

    # Freeze time to a fake monotonic just after the cache write so we're
    # well within the TTL window. ``time.time`` is what the module uses.
    cached_ts = fg._cache[0]  # type: ignore[index]
    monkeypatch.setattr(fg.time, "time", lambda: cached_ts + 60.0)

    # No respx mock installed → an actual HTTP call would raise. The cache
    # path must short-circuit before httpx is touched.
    second = await get_fear_greed_index()
    assert second is first


@pytest.mark.asyncio
async def test_refetches_after_cache_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(200, json=_SAMPLE_PAYLOAD),
        )
        first = await get_fear_greed_index()

    # Advance time past the 1h TTL.
    cached_ts = fg._cache[0]  # type: ignore[index]
    monkeypatch.setattr(
        fg.time, "time", lambda: cached_ts + fg._CACHE_TTL + 1.0,
    )

    refreshed_payload = {
        "data": [
            {
                "value": "30",
                "value_classification": "Fear",
                "timestamp": "1746576000",
            }
        ],
    }
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(200, json=refreshed_payload),
        )
        second = await get_fear_greed_index()

    assert route.called
    assert second.value == 30
    assert second.label == "Fear"
    assert second is not first


@pytest.mark.asyncio
async def test_network_error_propagates() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.alternative.me/fng/").mock(
            side_effect=httpx.ConnectError("boom"),
        )
        with pytest.raises(httpx.HTTPError):
            await get_fear_greed_index()

    # Cache must still be empty so the next call re-attempts.
    assert fg._cache is None


@pytest.mark.asyncio
async def test_http_5xx_propagates() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.alternative.me/fng/").mock(
            return_value=httpx.Response(500, text="bad gateway"),
        )
        with pytest.raises(httpx.HTTPError):
            await get_fear_greed_index()

    assert fg._cache is None
