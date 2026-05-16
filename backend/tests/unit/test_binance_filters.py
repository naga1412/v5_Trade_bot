"""Tests for app.exchanges.binance_filters — symbol filter cache + quantize.

The bug this module fixes: ``dispatcher._place_live_order`` was passing
the raw `(margin × leverage) / entry_price` float to Binance, which
rejects it with -1111 when the quantity isn't a multiple of the
symbol's LOT_SIZE.stepSize. This test suite asserts:

  - quantize_qty rounds DOWN to nearest step (never up — exceeding the
    operator's margin budget is worse than refusing a too-small order)
  - quantize_price rounds DOWN to nearest tick (LIMIT-mode use)
  - parsing /fapi/v1/exchangeInfo extracts the four filters we care
    about (LOT_SIZE.stepSize / minQty, PRICE_FILTER.tickSize,
    MIN_NOTIONAL.notional or minNotional — Binance uses both names)
  - get_symbol_filters caches (only one HTTP call even when multiple
    symbols are queried)
  - get_symbol_filters returns None when exchangeInfo errors (caller
    must refuse to submit the order, not retry blindly)
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.exchanges.binance_filters import (
    SymbolFilters,
    get_symbol_filters,
    quantize_price,
    quantize_qty,
    reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


# ---------------------------------------------------------------------------
# quantize_qty


def test_quantize_qty_rounds_down() -> None:
    # 0.001234 → 0.001 at step 0.001
    assert quantize_qty(0.001234, 0.001) == pytest.approx(0.001, abs=1e-9)


def test_quantize_qty_returns_zero_when_below_step() -> None:
    """Caller must check min_qty AFTER quantize — a below-step input
    yields 0.0, which is correctly < any positive min_qty."""
    assert quantize_qty(0.000999, 0.001) == pytest.approx(0.0, abs=1e-9)


def test_quantize_qty_exact_multiple_passes_through() -> None:
    assert quantize_qty(0.012, 0.001) == pytest.approx(0.012, abs=1e-9)


def test_quantize_qty_handles_float_imprecision() -> None:
    """0.7 / 0.1 == 6.9999... in float; naive floor would give 6.
    The +1e-9 epsilon in quantize_qty must save us."""
    assert quantize_qty(0.7, 0.1) == pytest.approx(0.7, abs=1e-9)


def test_quantize_qty_zero_step_returns_zero() -> None:
    """Defensive: Binance never returns step=0 but defaults shouldn't
    crash if filter parsing went wrong."""
    assert quantize_qty(0.5, 0.0) == 0.0


def test_quantize_qty_bug_example_from_prod() -> None:
    """The exact qty that triggered -1111 on 2026-05-16:
    margin 5 × lev 10 / mark 78404.20 ≈ 0.000637...
    Quantized to BTC step 0.001 → 0.000 → caller rejects (min_qty=0.001)."""
    raw = (5.0 * 10) / 78404.20
    quantized = quantize_qty(raw, 0.001)
    assert quantized == pytest.approx(0.0, abs=1e-9)
    # The caller would then see qty < min_qty (0.001) and refuse.


# ---------------------------------------------------------------------------
# quantize_price


def test_quantize_price_rounds_down() -> None:
    assert quantize_price(78972.39, 0.1) == pytest.approx(78972.3, abs=1e-3)


def test_quantize_price_zero_tick_returns_input() -> None:
    """Some symbols have tick=0 in our internal cache (when filter
    parsing skipped PRICE_FILTER) — pass through unchanged."""
    assert quantize_price(123.45, 0.0) == 123.45


# ---------------------------------------------------------------------------
# get_symbol_filters — parsing + caching


_EXCHANGE_INFO_FIXTURE: dict[str, Any] = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                # ETH uses minNotional (older name) — both must work.
                {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
            ],
        },
        {
            "symbol": "MISSINGFILTERS",
            # No LOT_SIZE filter — must be dropped by parser.
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            ],
        },
    ]
}


@pytest.mark.asyncio
async def test_get_symbol_filters_parses_BTCUSDT() -> None:
    """Happy path: parse exchangeInfo, extract BTCUSDT filters."""
    with respx.mock(
        base_url="https://fapi.binance.com",
    ) as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO_FIXTURE),
        )
        async with httpx.AsyncClient() as http:
            filt = await get_symbol_filters(
                "BTCUSDT", use_testnet=False, http=http,
            )

    assert filt is not None
    assert filt.symbol == "BTCUSDT"
    assert filt.step_size == pytest.approx(0.001)
    assert filt.min_qty == pytest.approx(0.001)
    assert filt.tick_size == pytest.approx(0.10)
    assert filt.min_notional == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_get_symbol_filters_handles_alt_minnotional_name() -> None:
    """Binance uses both 'notional' (newer) and 'minNotional' (older).
    The parser must accept either."""
    with respx.mock(base_url="https://fapi.binance.com") as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO_FIXTURE),
        )
        async with httpx.AsyncClient() as http:
            filt = await get_symbol_filters(
                "ETHUSDT", use_testnet=False, http=http,
            )
    assert filt is not None
    assert filt.min_notional == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_get_symbol_filters_returns_none_on_unknown_symbol() -> None:
    """Symbol not in exchangeInfo → None. Caller refuses to submit."""
    with respx.mock(base_url="https://fapi.binance.com") as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO_FIXTURE),
        )
        async with httpx.AsyncClient() as http:
            filt = await get_symbol_filters(
                "DOESNOTEXIST", use_testnet=False, http=http,
            )
    assert filt is None


@pytest.mark.asyncio
async def test_get_symbol_filters_drops_symbols_missing_lot_size() -> None:
    """A symbol without LOT_SIZE filter is unusable — must NOT be cached
    (else a stale 0-step would silently corrupt qty math)."""
    with respx.mock(base_url="https://fapi.binance.com") as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO_FIXTURE),
        )
        async with httpx.AsyncClient() as http:
            filt = await get_symbol_filters(
                "MISSINGFILTERS", use_testnet=False, http=http,
            )
    assert filt is None


@pytest.mark.asyncio
async def test_get_symbol_filters_caches_after_first_fetch() -> None:
    """Two lookups → one HTTP call. The exchangeInfo body covers the
    whole exchange so a single fetch satisfies all symbol lookups."""
    call_count = 0

    def _spy(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_EXCHANGE_INFO_FIXTURE)

    with respx.mock(base_url="https://fapi.binance.com") as router:
        router.get("/fapi/v1/exchangeInfo").mock(side_effect=_spy)
        async with httpx.AsyncClient() as http:
            await get_symbol_filters("BTCUSDT", use_testnet=False, http=http)
            await get_symbol_filters("ETHUSDT", use_testnet=False, http=http)
            await get_symbol_filters("BTCUSDT", use_testnet=False, http=http)
    assert call_count == 1


@pytest.mark.asyncio
async def test_get_symbol_filters_returns_none_on_http_error() -> None:
    """Network blip / 500 from Binance → None, NOT a raise. Caller
    must refuse to submit (a placed order that bypasses filter check
    would risk -1111 from Binance OR submit a wildly wrong qty)."""
    with respx.mock(base_url="https://fapi.binance.com") as router:
        router.get("/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(500, json={"msg": "server error"}),
        )
        async with httpx.AsyncClient() as http:
            filt = await get_symbol_filters(
                "BTCUSDT", use_testnet=False, http=http,
            )
    assert filt is None


@pytest.mark.asyncio
async def test_testnet_and_live_use_separate_caches() -> None:
    """Same symbol on testnet vs live could in theory have different
    filters (or the testnet endpoint could be down independently).
    Verify the cache key includes the env."""
    live_body = _EXCHANGE_INFO_FIXTURE
    testnet_body = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    # Pretend testnet has a coarser step for testing
                    {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
        ],
    }
    with respx.mock() as router:
        router.get("https://fapi.binance.com/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=live_body),
        )
        router.get("https://testnet.binancefuture.com/fapi/v1/exchangeInfo").mock(
            return_value=httpx.Response(200, json=testnet_body),
        )
        async with httpx.AsyncClient() as http:
            live_filt = await get_symbol_filters(
                "BTCUSDT", use_testnet=False, http=http,
            )
            testnet_filt = await get_symbol_filters(
                "BTCUSDT", use_testnet=True, http=http,
            )
    assert live_filt is not None
    assert testnet_filt is not None
    assert live_filt.step_size == pytest.approx(0.001)
    assert testnet_filt.step_size == pytest.approx(0.01)
