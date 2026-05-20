"""PR10.7: fetch_spot_prices resilience tests.

Diagnostic run 26147552565 showed that pre-PR10.7 the function returns
an empty dict whenever Binance SPOT 400-rejects the batch — and Binance
rejects the whole batch if ANY symbol is invalid (duplicates, futures-
only, delisted, region-restricted). This blanked the Open Positions
card's Now/P&L for all 20 open positions in prod.

Post-PR10.7:
- Input symbols are deduped + blacklist-filtered upfront.
- On batch 400, the function falls back to per-symbol fetches and
  returns the partial dict (skipping the bad symbols).
- Per-symbol failures are counted in module-level metrics.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.data.binance_ticker import (
    _reset_metrics_for_tests,
    fetch_spot_prices,
    get_metrics_snapshot,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    _reset_metrics_for_tests()


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://api.binance.com")


def _ok_batch(prices: dict[str, float]) -> httpx.Response:
    body = [{"symbol": s, "price": str(p)} for s, p in prices.items()]
    return httpx.Response(200, json=body)


def _ok_single(price: float) -> httpx.Response:
    return httpx.Response(200, json={"symbol": "X", "price": str(price)})


@pytest.mark.asyncio
async def test_empty_input_returns_empty_dict() -> None:
    async with _client(lambda req: httpx.Response(500)) as http:
        out = await fetch_spot_prices([], http=http)
    assert out == {}


@pytest.mark.asyncio
async def test_dedupe_symbols_in_batch() -> None:
    """Input [BTCUSDT, ETHUSDT, BTCUSDT] → batch URL contains BTCUSDT once."""
    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        # Track what was sent in the symbols param.
        symbols_json = req.url.params.get("symbols", "[]")
        captured.append(symbols_json)
        symbols = json.loads(symbols_json)
        body = [{"symbol": s, "price": "1.0"} for s in symbols]
        return httpx.Response(200, json=body)

    async with _client(handler) as http:
        out = await fetch_spot_prices(
            ["BTCUSDT", "ETHUSDT", "BTCUSDT"], http=http,
        )

    assert len(captured) == 1, "should be one batch call"
    sent = json.loads(captured[0])
    assert sent == ["BTCUSDT", "ETHUSDT"], "deduped + sorted"
    assert set(out) == {"BTCUSDT", "ETHUSDT"}


@pytest.mark.asyncio
async def test_batch_success_returns_full_dict() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_batch({"BTCUSDT": 50000.0, "ETHUSDT": 3000.0, "TONUSDT": 5.5})

    async with _client(handler) as http:
        out = await fetch_spot_prices(
            ["BTCUSDT", "ETHUSDT", "TONUSDT"], http=http,
        )

    assert out == {"BTCUSDT": 50000.0, "ETHUSDT": 3000.0, "TONUSDT": 5.5}
    snap = get_metrics_snapshot()
    assert snap["spot_price_fetch_batch_rejected_total"] == 0


@pytest.mark.asyncio
async def test_batch_400_falls_back_per_symbol_partial_success() -> None:
    """Batch 400, then per-symbol calls succeed for 2 of 3 → partial dict."""
    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        params = dict(req.url.params)
        if "symbols" in params:
            call_log.append("batch")
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        # per-symbol fallback
        sym = params.get("symbol", "")
        call_log.append(f"single:{sym}")
        if sym == "EDENUSDT":
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        prices = {"BTCUSDT": 50000.0, "ETHUSDT": 3000.0}
        if sym in prices:
            return httpx.Response(200, json={"symbol": sym, "price": str(prices[sym])})
        return httpx.Response(404)

    async with _client(handler) as http:
        out = await fetch_spot_prices(
            ["BTCUSDT", "ETHUSDT", "EDENUSDT"], http=http,
        )

    # BTC + ETH should be returned, EDEN absent (per-symbol failure).
    # NOTE: EDENUSDT is in SHADOW_SPOT_BLACKLIST so it will be filtered
    # at the dedupe step — never hits the network. That's the intent
    # (FIX 2 + FIX 1 belt-and-suspenders), so the per-symbol call count
    # for EDENUSDT is 0. Adjust expectation: batch is just BTC+ETH.
    assert "BTCUSDT" in out
    assert "ETHUSDT" in out
    assert "EDENUSDT" not in out


@pytest.mark.asyncio
async def test_batch_400_falls_back_with_non_blacklisted_invalid_symbol() -> None:
    """Symbol NOT on blacklist but still invalid on SPOT → fallback handles it."""
    call_log: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        if "symbols" in params:
            call_log.append("batch")
            return httpx.Response(400, json={"msg": "Invalid symbol."})
        sym = params.get("symbol", "")
        call_log.append(f"single:{sym}")
        if sym == "WEIRDUSDT":
            return httpx.Response(400, json={"msg": "Invalid symbol."})
        prices = {"BTCUSDT": 50000.0}
        if sym in prices:
            return httpx.Response(200, json={"symbol": sym, "price": str(prices[sym])})
        return httpx.Response(404)

    async with _client(handler) as http:
        out = await fetch_spot_prices(["BTCUSDT", "WEIRDUSDT"], http=http)

    assert out == {"BTCUSDT": 50000.0}, "BTC alone, WEIRD absent"
    assert "batch" in call_log
    assert "single:BTCUSDT" in call_log
    assert "single:WEIRDUSDT" in call_log
    snap = get_metrics_snapshot()
    assert snap["spot_price_fetch_batch_rejected_total"] == 1
    assert snap["spot_price_fetch_per_symbol_fallback_total"] == 1
    failed = snap["spot_price_fetch_per_symbol_failed"]
    assert isinstance(failed, dict)
    assert failed.get("WEIRDUSDT", 0) == 1


@pytest.mark.asyncio
async def test_per_symbol_all_fail_returns_empty_dict() -> None:
    """All per-symbol calls 400 → empty dict, batch counter incremented."""
    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        if "symbols" in params:
            return httpx.Response(400)
        return httpx.Response(400)

    async with _client(handler) as http:
        out = await fetch_spot_prices(["AAA1USDT", "BBB2USDT"], http=http)

    assert out == {}
    snap = get_metrics_snapshot()
    assert snap["spot_price_fetch_batch_rejected_total"] == 1


@pytest.mark.asyncio
async def test_per_symbol_network_error_returns_partial() -> None:
    """One per-symbol call raises httpx.ConnectError; others succeed → partial."""
    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        if "symbols" in params:
            return httpx.Response(400)
        sym = params.get("symbol", "")
        if sym == "BTCUSDT":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.0"})
        # ETHUSDT triggers a connect error
        raise httpx.ConnectError("simulated network blip")

    async with _client(handler) as http:
        out = await fetch_spot_prices(["BTCUSDT", "ETHUSDT"], http=http)

    assert out == {"BTCUSDT": 50000.0}
    snap = get_metrics_snapshot()
    failed = snap["spot_price_fetch_per_symbol_failed"]
    assert isinstance(failed, dict)
    assert failed.get("ETHUSDT", 0) == 1


@pytest.mark.asyncio
async def test_blacklist_filters_before_batch() -> None:
    """SHADOW_SPOT_BLACKLIST symbols never reach the network."""
    captured_symbols: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        s = req.url.params.get("symbols", "[]")
        captured_symbols.append(json.loads(s))
        return _ok_batch({"BTCUSDT": 50000.0})

    async with _client(handler) as http:
        out = await fetch_spot_prices(
            ["BTCUSDT", "EDENUSDT", "LUNCUSDT", "UUSDT"], http=http,
        )

    assert captured_symbols == [["BTCUSDT"]], "blacklist filtered upfront"
    assert out == {"BTCUSDT": 50000.0}


@pytest.mark.asyncio
async def test_blacklist_only_input_returns_empty() -> None:
    """If every input symbol is blacklisted, no HTTP call is made."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500)

    async with _client(handler) as http:
        out = await fetch_spot_prices(["EDENUSDT", "LUNCUSDT"], http=http)

    assert out == {}
    assert call_count == 0, "no HTTP call when everything is blacklisted"
