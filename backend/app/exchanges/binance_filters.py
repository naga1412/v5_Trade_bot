"""Per-symbol filter cache + qty/price quantizers for Binance Futures.

Binance Futures REST + WS calls reject orders whose ``quantity`` is not
a multiple of the symbol's ``stepSize`` (filter LOT_SIZE) with HTTP 400
``-1111: "Precision is over the maximum defined for this asset"``. The
same applies to ``price`` and ``tickSize`` (PRICE_FILTER), and orders
whose ``quantity * price`` falls under ``minNotional`` (MIN_NOTIONAL)
are rejected with a different code.

This module fetches ``GET /fapi/v1/exchangeInfo`` once per environment
(testnet vs live) and caches the relevant filters per symbol. Callers
use ``quantize_qty / quantize_price`` to round DOWN to the nearest
allowed step + the minQty / minNotional gates to refuse a too-small
order BEFORE it hits Binance.

Discovery: the original ``_place_live_order`` in
``app.trading.execution.dispatcher`` computed ``qty = (margin × lev)
/ entry_price`` and submitted the raw float. Binance rejected every
single fully-auto trade with -1111. Found 2026-05-16 via the
``test-trade-fully-auto-round-trip`` smoke probe.

Why no TTL on the cache: Binance changes lot/tick sizes very rarely
and the operator-side impact of a stale filter is at worst one
rejected order. A daily refresh would protect against that — but at
the cost of an extra HTTP call per day per environment, and the
operator can always restart the backend to pick up the latest.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import httpx


log = logging.getLogger(__name__)


_LIVE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
_TESTNET_INFO_URL = "https://testnet.binancefuture.com/fapi/v1/exchangeInfo"

# (env_label, symbol) → SymbolFilters. env_label is "testnet" or "live".
_FILTERS_CACHE: dict[tuple[str, str], "SymbolFilters"] = {}


@dataclass(frozen=True)
class SymbolFilters:
    """The four filters that gate a Binance Futures order submission.

    Values are read directly from /fapi/v1/exchangeInfo so semantics
    match Binance's own validator exactly.
    """

    symbol: str
    step_size: float       # LOT_SIZE.stepSize — qty must be multiple of this
    min_qty: float         # LOT_SIZE.minQty
    tick_size: float       # PRICE_FILTER.tickSize — price must be multiple
    min_notional: float    # MIN_NOTIONAL.notional (qty * price floor)


def _env_label(use_testnet: bool) -> str:
    return "testnet" if use_testnet else "live"


def _info_url(use_testnet: bool) -> str:
    return _TESTNET_INFO_URL if use_testnet else _LIVE_INFO_URL


async def _fetch_exchange_info(
    use_testnet: bool, *, http: httpx.AsyncClient | None = None,
) -> dict[str, SymbolFilters]:
    """Fetch + parse /fapi/v1/exchangeInfo for one environment.

    Returns a fresh dict; caller decides whether to merge into the
    module cache. Owns the client lifecycle iff ``http`` is None.
    """
    url = _info_url(use_testnet)
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=15.0)
    try:
        r = await client.get(url)
        r.raise_for_status()
        body = r.json()
    finally:
        if owns_http:
            await client.aclose()

    out: dict[str, SymbolFilters] = {}
    for sym_entry in body.get("symbols", []):
        symbol = sym_entry.get("symbol")
        if not symbol:
            continue
        step_size = min_qty = tick_size = min_notional = 0.0
        for f in sym_entry.get("filters", []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                step_size = float(f.get("stepSize") or 0)
                min_qty = float(f.get("minQty") or 0)
            elif ft == "PRICE_FILTER":
                tick_size = float(f.get("tickSize") or 0)
            elif ft == "MIN_NOTIONAL":
                # Some symbols use "notional", some use "minNotional".
                min_notional = float(
                    f.get("notional") or f.get("minNotional") or 0,
                )
        if step_size > 0 and min_qty > 0:
            out[symbol] = SymbolFilters(
                symbol=symbol,
                step_size=step_size,
                min_qty=min_qty,
                tick_size=tick_size,
                min_notional=min_notional,
            )
    return out


async def get_symbol_filters(
    symbol: str, *, use_testnet: bool,
    http: httpx.AsyncClient | None = None,
) -> SymbolFilters | None:
    """Cached lookup of filters for one symbol.

    Returns None if the symbol isn't listed on the exchange (which is
    a real possibility — e.g. delisted, restricted region, or a typo).
    Caller should treat None as "refuse to submit the order".

    Thread-safety: the module-level cache is read-after-write here but
    we're in single-event-loop async land so no lock needed.
    """
    env = _env_label(use_testnet)
    cached = _FILTERS_CACHE.get((env, symbol))
    if cached is not None:
        return cached
    # Cache miss → fetch the WHOLE exchangeInfo and store every symbol
    # at once. Cheaper than per-symbol fetches when the caller will
    # eventually look up many symbols (universe of 30).
    try:
        fresh = await _fetch_exchange_info(use_testnet, http=http)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.error(
            "binance_filters: exchangeInfo fetch failed (%s); cannot "
            "validate orders for %s", e, symbol,
        )
        return None
    for sym, filt in fresh.items():
        _FILTERS_CACHE[(env, sym)] = filt
    return _FILTERS_CACHE.get((env, symbol))


def quantize_qty(qty: float, step_size: float) -> float:
    """Round qty DOWN to the nearest stepSize multiple.

    Always rounds DOWN so that an order computed from an exact
    margin*leverage budget never exceeds the budget. Returns 0.0 if
    step_size is 0 (Binance never returns that, but defensive).

    Examples (step=0.001):
      0.001234 -> 0.001
      0.000999 -> 0.000  (caller must check min_qty separately)
      0.012345 -> 0.012
    """
    if step_size <= 0:
        return 0.0
    # Use Decimal-like floor via math.floor to avoid float-rounding
    # weirdness: 0.7 / 0.1 == 6.999... → would floor to 6, not 7.
    n = math.floor(qty / step_size + 1e-9)
    return n * step_size


def quantize_price(price: float, tick_size: float) -> float:
    """Round price DOWN to the nearest tickSize multiple.

    Used for LIMIT orders. MARKET orders ignore price entirely on the
    Binance side, but tests + LIMIT-mode code paths still want this.
    """
    if tick_size <= 0:
        return price
    n = math.floor(price / tick_size + 1e-9)
    return n * tick_size


def reset_cache_for_tests() -> None:
    """Test fixture only — never call from prod code."""
    _FILTERS_CACHE.clear()


__all__ = [
    "SymbolFilters",
    "get_symbol_filters",
    "quantize_price",
    "quantize_qty",
    "reset_cache_for_tests",
]
