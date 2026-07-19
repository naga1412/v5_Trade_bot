"""W4 flow_features — Binance Futures order-flow context (brain supervisor expansion).

Records three per-symbol features from Binance Futures endpoints:
  ls_account_ratio     — Long/(Long+Short) accounts [0, 1]
  taker_buy_sell_ratio — buy_vol/(buy_vol+sell_vol) from taker flow [0, 1]
  oi_4h_delta          — (oi_now - oi_4h_ago) / oi_4h_ago (fractional)

Architecture: module-level per-symbol cache (_FLOW_CACHE).
  - Shadow worker calls update_flow_cache(symbol) as a fire-and-forget task
    after processing each 1h candle; cache is fresh for the next observation.
  - compute(symbol) is a sync read from the cache — same call site at both
    observation.py (training data capture) and predictor.py (inference).
  - Staleness bound: at most 1h (one missed candle); immaterial for slow
    order-flow signals.

Graceful failure: any HTTP error or missing field sets that key to None.
The shadow worker continues normally; None → 0.0 at obs assembly (Phase 2).
"""
from __future__ import annotations

import logging
from typing import Final

import httpx

log = logging.getLogger(__name__)

_BASE_URL: Final = "https://fapi.binance.com"
_TIMEOUT: Final = 10.0

_NULL: Final[dict[str, float | None]] = {
    "ls_account_ratio": None,
    "taker_buy_sell_ratio": None,
    "oi_4h_delta": None,
}

_FLOW_CACHE: dict[str, dict[str, float | None]] = {}


def compute(symbol: str) -> dict[str, float | None]:
    """Return cached flow features for *symbol*.

    Returns all-None if update_flow_cache has not yet been called for this
    symbol (first observation per process lifetime).
    """
    return dict(_FLOW_CACHE.get(symbol, _NULL))


def get_cached(symbol: str) -> dict[str, float | None]:
    """Alias for compute() — for test introspection."""
    return compute(symbol)


def _clear_cache_for_tests() -> None:
    _FLOW_CACHE.clear()


async def update_flow_cache(
    symbol: str,
    *,
    http: httpx.AsyncClient | None = None,
    base_url: str = _BASE_URL,
) -> None:
    """Fetch Binance Futures order-flow data for *symbol* and refresh cache.

    Designed for fire-and-forget: ``asyncio.create_task(update_flow_cache(sym))``.
    Any network or parse error is caught and logged at DEBUG; partial results
    are stored (a failed endpoint leaves its key as None while others succeed).

    Reuses an injected httpx.AsyncClient when provided (preferred in the
    shadow worker to reuse the shared connection pool). Falls back to a
    short-lived client when called standalone (e.g. from tests).
    """
    native = symbol.replace("/", "")
    result: dict[str, float | None] = dict(_NULL)

    close_http = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        # 1. Long/Short account ratio
        try:
            resp = await http.get(
                f"{base_url}/futures/data/globalLongShortAccountRatio",
                params={"symbol": native, "period": "5m", "limit": "1"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                result["ls_account_ratio"] = float(rows[0]["longAccount"])
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features ls_account_ratio error %s: %s", native, exc)

        # 2. Taker buy-sell ratio (two separate endpoints per spec §3.5)
        try:
            r_buy = await http.get(
                f"{base_url}/futures/data/takerbuyvolume",
                params={"symbol": native, "period": "5m", "limit": "1"},
            )
            r_sell = await http.get(
                f"{base_url}/futures/data/takersellvolume",
                params={"symbol": native, "period": "5m", "limit": "1"},
            )
            r_buy.raise_for_status()
            r_sell.raise_for_status()
            rows_buy = r_buy.json()
            rows_sell = r_sell.json()
            if rows_buy and rows_sell:
                buy_vol = float(rows_buy[0]["buyVol"])
                sell_vol = float(rows_sell[0]["sellVol"])
                total = buy_vol + sell_vol
                if total > 0:
                    result["taker_buy_sell_ratio"] = buy_vol / total
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features taker_ratio error %s: %s", native, exc)

        # 3. OI 4h delta: need 5 × 1h buckets (indices 0..4 → 4h span)
        try:
            resp = await http.get(
                f"{base_url}/fapi/v1/openInterestHist",
                params={"symbol": native, "period": "1h", "limit": "5"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if len(rows) >= 5:
                oi_now = float(rows[-1]["sumOpenInterest"])
                oi_4h = float(rows[0]["sumOpenInterest"])
                if oi_4h > 0:
                    result["oi_4h_delta"] = (oi_now - oi_4h) / oi_4h
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features oi_4h_delta error %s: %s", native, exc)

        _FLOW_CACHE[symbol] = result
    finally:
        if close_http:
            await http.aclose()


__all__ = [
    "compute",
    "get_cached",
    "update_flow_cache",
]
