"""PR10.7: SHADOW_SPOT_BLACKLIST filters the asset_universe.

The universe top-N fetcher reads Binance SPOT 24h ticker. Some symbols
still listed in /24hr fail on /ticker/price (region restrictions,
status anomalies). PR10.7 adds a config-level blacklist that filters
those symbols out of the universe before they enter the asset table
or the dispatcher.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.shadow.universe import fetch_top_n_usdt_spot


@pytest.fixture
def _spot_blacklist_patch():
    """Patch get_settings to return a known blacklist for these tests."""
    from types import SimpleNamespace
    fake = SimpleNamespace(SHADOW_SPOT_BLACKLIST=[
        "EDENUSDT", "LUNCUSDT", "PAXGUSDT", "XAUTUSDT", "UUSDT", "USDCUSDT",
    ])
    with patch("app.config.get_settings", return_value=fake):
        yield


@pytest.mark.asyncio
async def test_universe_filters_blacklisted_symbols(_spot_blacklist_patch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
            {"symbol": "EDENUSDT", "quoteVolume": "500000"},
            {"symbol": "ETHUSDT", "quoteVolume": "900000"},
            {"symbol": "LUNCUSDT", "quoteVolume": "400000"},
            {"symbol": "SOLUSDT", "quoteVolume": "800000"},
            {"symbol": "UUSDT", "quoteVolume": "100000"},
            {"symbol": "USDCUSDT", "quoteVolume": "50000"},
            {"symbol": "TONUSDT", "quoteVolume": "200000"},
        ])
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        entries = await fetch_top_n_usdt_spot(http, n=10)

    symbols = [e.symbol for e in entries]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert "SOLUSDT" in symbols
    assert "TONUSDT" in symbols
    assert "EDENUSDT" not in symbols, "blacklisted (futures-only)"
    assert "LUNCUSDT" not in symbols, "blacklisted (delisted from SPOT)"
    assert "UUSDT" not in symbols, "blacklisted (suspect)"
    assert "USDCUSDT" not in symbols, "blacklisted (stablecoin)"
    # Volume-sorted, so order should be BTC > ETH > SOL > TON
    assert entries[0].symbol == "BTCUSDT"
    assert entries[0].rank == 1


@pytest.mark.asyncio
async def test_universe_excludes_non_usdt_pairs(_spot_blacklist_patch) -> None:
    """Sanity: pre-PR10.7 behavior preserved — only -USDT pairs."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
            {"symbol": "BTCBUSD", "quoteVolume": "999999"},
            {"symbol": "BTCETH", "quoteVolume": "500000"},
        ])
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        entries = await fetch_top_n_usdt_spot(http, n=10)

    symbols = [e.symbol for e in entries]
    assert symbols == ["BTCUSDT"]
