"""Tests for app/data/binance_ticker.py — batch SPOT price fetch + pnl math.

The fetcher must:
  - skip the HTTP call entirely when given no symbols
  - successfully parse a valid batch response
  - return an empty dict on HTTP error (best-effort contract)
  - return a partial dict when SOME entries are malformed
  - handle non-list responses defensively

The pnl helper must implement the LONG / SHORT sign convention exactly
and handle invalid entry prices.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.data.binance_ticker import (
    compute_unrealized_pnl_pct,
    fetch_spot_prices,
)


# ---------------------------------------------------------------------------
# compute_unrealized_pnl_pct — pure function.


def test_pnl_long_positive_when_price_above_entry() -> None:
    pct = compute_unrealized_pnl_pct(
        direction="LONG", entry_price=100.0, current_price=102.0,
    )
    assert pct == pytest.approx(2.0)


def test_pnl_long_negative_when_price_below_entry() -> None:
    pct = compute_unrealized_pnl_pct(
        direction="LONG", entry_price=100.0, current_price=98.5,
    )
    assert pct == pytest.approx(-1.5)


def test_pnl_short_positive_when_price_below_entry() -> None:
    """SHORT positions profit when price drops — sign flipped vs LONG."""
    pct = compute_unrealized_pnl_pct(
        direction="SHORT", entry_price=2245.41, current_price=2198.75,
    )
    assert pct == pytest.approx(
        (2245.41 - 2198.75) / 2245.41 * 100.0, abs=1e-6,
    )
    assert pct > 0


def test_pnl_short_negative_when_price_above_entry() -> None:
    pct = compute_unrealized_pnl_pct(
        direction="SHORT", entry_price=2245.41, current_price=2268.74,
    )
    assert pct < 0


def test_pnl_zero_entry_returns_none() -> None:
    """Defensive: a 0 entry would divide by zero, so return None."""
    assert compute_unrealized_pnl_pct(
        direction="LONG", entry_price=0.0, current_price=100.0,
    ) is None
    assert compute_unrealized_pnl_pct(
        direction="LONG", entry_price=-1.0, current_price=100.0,
    ) is None


def test_pnl_unknown_direction_returns_none() -> None:
    assert compute_unrealized_pnl_pct(
        direction="NEUTRAL", entry_price=100.0, current_price=101.0,
    ) is None


# ---------------------------------------------------------------------------
# fetch_spot_prices — async, exercises respx-mocked HTTP.


@pytest.mark.asyncio
async def test_empty_symbols_skips_http_entirely() -> None:
    """Passing an empty list must not make an HTTP call (saves a round-trip)."""
    async with httpx.AsyncClient() as http:
        # respx in strict mode would raise if any request is made — we
        # rely on the assertion that the call returns immediately.
        with respx.mock(assert_all_called=False) as router:
            router.get("https://api.binance.com/api/v3/ticker/price").mock(
                return_value=httpx.Response(500, json={}),
            )
            out = await fetch_spot_prices([], http=http)
    assert out == {}


@pytest.mark.asyncio
async def test_happy_path_returns_symbol_price_map() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(
            return_value=httpx.Response(200, json=[
                {"symbol": "BTCUSDT", "price": "78521.31"},
                {"symbol": "ETHUSDT", "price": "2213.55"},
            ]),
        )
        out = await fetch_spot_prices(["BTCUSDT", "ETHUSDT"], http=http)
    assert out == {"BTCUSDT": pytest.approx(78521.31), "ETHUSDT": pytest.approx(2213.55)}


@pytest.mark.asyncio
async def test_symbols_param_passes_as_compact_json_array() -> None:
    """Binance is strict about the symbols param: it must be a JSON-encoded
    array of strings with NO whitespace between elements. The default
    `json.dumps` separators inject a space after each comma, which
    URL-encodes to `+`/`%20` and triggers a 400 on Binance's end.

    Wire format must be: `["BTCUSDT","ETHUSDT"]`
    NOT:                 `["BTCUSDT", "ETHUSDT"]`
    """
    captured_request: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_request.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(side_effect=_record)
        await fetch_spot_prices(["BTCUSDT", "ETHUSDT"], http=http)

    assert len(captured_request) == 1
    raw = captured_request[0].url.params.get("symbols")
    assert raw is not None
    # Logical: parses back to the same list
    parsed = json.loads(raw)
    assert parsed == ["BTCUSDT", "ETHUSDT"]
    # Wire-format pin: NO space between elements (the regression we just hit).
    assert raw == '["BTCUSDT","ETHUSDT"]'
    assert " " not in raw  # belt + braces — any whitespace breaks Binance


@pytest.mark.asyncio
async def test_http_error_returns_empty_dict() -> None:
    """500 from Binance must NOT propagate — best-effort contract."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(
            return_value=httpx.Response(500, json={"msg": "Internal"}),
        )
        out = await fetch_spot_prices(["BTCUSDT"], http=http)
    assert out == {}


@pytest.mark.asyncio
async def test_network_error_returns_empty_dict() -> None:
    """Connection errors should be swallowed."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        out = await fetch_spot_prices(["BTCUSDT"], http=http)
    assert out == {}


@pytest.mark.asyncio
async def test_partial_malformed_entries_filtered_out() -> None:
    """A mixed response (some good, some malformed) returns just the good ones."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(
            return_value=httpx.Response(200, json=[
                {"symbol": "BTCUSDT", "price": "100.0"},
                {"price": "200.0"},                      # missing symbol
                {"symbol": "ETHUSDT", "price": "not-a-number"},  # bad price
                {"symbol": "BNBUSDT", "price": "650.0"},
            ]),
        )
        out = await fetch_spot_prices(
            ["BTCUSDT", "ETHUSDT", "BNBUSDT"], http=http,
        )
    assert out == {
        "BTCUSDT": pytest.approx(100.0),
        "BNBUSDT": pytest.approx(650.0),
    }


@pytest.mark.asyncio
async def test_non_list_response_returns_empty() -> None:
    """If Binance returns an error object (not a list), return empty dict."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/ticker/price").mock(
            return_value=httpx.Response(200, json={"code": -1121, "msg": "Invalid"}),
        )
        out = await fetch_spot_prices(["BADUSDT"], http=http)
    assert out == {}
