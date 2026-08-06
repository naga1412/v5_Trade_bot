"""W4 flow_features — Binance Futures order-flow context (brain supervisor expansion).

Records four per-symbol features from Binance Futures endpoints:
  ls_account_ratio     — Long/(Long+Short) accounts [0, 1]
  taker_buy_sell_ratio — buy_vol/(buy_vol+sell_vol) from taker flow [0, 1]
  oi_4h_delta          — (oi_now - oi_4h_ago) / oi_4h_ago (fractional)
  oi_24h_delta         — (oi_now - oi_24h_ago) / oi_24h_ago (fractional)

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
import sqlalchemy as sa

from app.db.session import get_session_factory

log = logging.getLogger(__name__)

_BASE_URL: Final = "https://fapi.binance.com"
_TIMEOUT: Final = 10.0

_NULL: Final[dict[str, float | None]] = {
    "ls_account_ratio": None,
    "taker_buy_sell_ratio": None,
    "oi_4h_delta": None,
    "oi_24h_delta": None,
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

        # 2. Taker buy-sell ratio. The spec's original two-endpoint design
        # (/futures/data/takerbuyvolume + takersellvolume) does not exist on
        # Binance's API — both 404 (confirmed via direct curl 2026-08-06,
        # see item-3c movers-vs-control probe output: n=0 for every symbol
        # despite funding/OI working fine on the same base URL). This has
        # silently left taker_buy_sell_ratio None in every prediction and
        # shadow_observations row since W4 shipped. The real, single,
        # correct endpoint is /futures/data/takerlongshortRatio, which
        # returns buyVol + sellVol together.
        try:
            resp = await http.get(
                f"{base_url}/futures/data/takerlongshortRatio",
                params={"symbol": native, "period": "5m", "limit": "1"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                buy_vol = float(rows[0]["buyVol"])
                sell_vol = float(rows[0]["sellVol"])
                total = buy_vol + sell_vol
                if total > 0:
                    result["taker_buy_sell_ratio"] = buy_vol / total
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features taker_ratio error %s: %s", native, exc)

        # 3. OI 4h + 24h delta from ONE call: 25 x 1h buckets covers a
        # 24h span (indices 0..24), and the last 5 of those (indices
        # -5..-1) already cover the 4h span — zero extra API cost for
        # the second window. item-1 decisive analysis (2026-08-06)
        # found real, weak, non-transformative separation in OI delta
        # (best precision ~32% at 15.5% recall vs a 22.48% base rate);
        # this collects both windows going forward for FORWARD
        # validation, storage only, not wired into scoring.
        #
        # ENDPOINT BUG (found 2026-08-06 via the item-3 endpoint audit,
        # same class as the taker-ratio fix): this called
        # /fapi/v1/openInterestHist, which 404s -- confirmed live. The
        # correct path is /futures/data/openInterestHist, already fixed
        # in the sibling module app/data/adapters/binance_futures_
        # intermarket.py by PR #393, but never applied here. oi_4h_delta
        # has been silently None for every symbol, every prediction and
        # shadow_observations row, since W4 shipped -- a third instance
        # of the identical failure mode, discovered while extending this
        # exact call for oi_24h_delta.
        try:
            resp = await http.get(
                f"{base_url}/futures/data/openInterestHist",
                params={"symbol": native, "period": "1h", "limit": "25"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if len(rows) >= 5:
                oi_now = float(rows[-1]["sumOpenInterest"])
                oi_4h = float(rows[-5]["sumOpenInterest"])
                if oi_4h > 0:
                    result["oi_4h_delta"] = (oi_now - oi_4h) / oi_4h
            if len(rows) >= 25:
                oi_now = float(rows[-1]["sumOpenInterest"])
                oi_24h = float(rows[0]["sumOpenInterest"])
                if oi_24h > 0:
                    result["oi_24h_delta"] = (oi_now - oi_24h) / oi_24h
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features oi_delta error %s: %s", native, exc)

        _FLOW_CACHE[symbol] = result
    finally:
        if close_http:
            await http.aclose()


async def update_flow_cache_and_persist(
    symbol: str,
    *,
    http: httpx.AsyncClient | None = None,
    base_url: str = _BASE_URL,
) -> None:
    """Refresh the in-process cache, then durably record the same values.

    Storage only — this table is not read by any scoring or gating path.
    `update_flow_cache` already fetches these 3 values on every 1h shadow
    candle across the universe but only ever kept them in `_FLOW_CACHE`
    (lost on restart) or captured them once per symbol at shadow-trade-open
    time (`shadow_observations`, sparse). This gives the same already-
    fetched values a continuous per-symbol time series at zero new API
    cost. Best-effort: a persistence failure is logged and swallowed, never
    propagated — this must not affect the fire-and-forget caller.

    Deliberately uses the app's own global session factory
    (`app.db.session.get_session_factory`) rather than accepting an
    injected one from the caller: this is a fire-and-forget background
    task, so it must never share a connection pool with — or become a
    second concurrent consumer of — whatever session_factory the caller
    (e.g. the shadow worker) was constructed with. In a test harness that
    injects its own isolated DB (SQLite `:memory:`), a second concurrent
    connection into that same pool is a different, empty database —
    silently corrupting the caller's own subsequent reads/writes. Using
    the global factory keeps this telemetry write fully decoupled.
    """
    await update_flow_cache(symbol, http=http, base_url=base_url)
    result = compute(symbol)
    if all(v is None for v in result.values()):
        return
    try:
        async with get_session_factory()() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO flow_feature_snapshots "
                    "(symbol, ls_account_ratio, taker_buy_sell_ratio, "
                    " oi_4h_delta, oi_24h_delta) "
                    "VALUES (:s, :ls, :tbsr, :oi4, :oi24)"
                ),
                {
                    "s": symbol,
                    "ls": result["ls_account_ratio"],
                    "tbsr": result["taker_buy_sell_ratio"],
                    "oi4": result["oi_4h_delta"],
                    "oi24": result["oi_24h_delta"],
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("flow_feature_snapshots persist failed for %s: %s", symbol, exc)


__all__ = [
    "compute",
    "get_cached",
    "update_flow_cache",
    "update_flow_cache_and_persist",
]
