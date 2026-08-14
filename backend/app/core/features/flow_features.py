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

FU-40 (remediation work order B4, 2026-08-14): every production call goes
through the shared Binance Futures ``RateLimitedClient`` singleton (the
same one ``app.data.adapters.binance_futures_intermarket`` uses for
premiumIndex/openInterestHist) instead of a fresh, unthrottled
``httpx.AsyncClient`` per call. The old default path built a brand-new
client and closed it again on every single 1h-candle tick across the
whole universe — no connection reuse, and completely invisible to
Binance's real IP-level weight limit, stacking on top of whatever the
intermarket adapter was already consuming against the same limit.
Callers that inject their own ``http=`` (tests, standalone scripts) are
unaffected — they get a permissive non-blocking bucket wrapping that
client, preserving today's behavior exactly.
"""
from __future__ import annotations

import logging
from typing import Final

import httpx
import sqlalchemy as sa

from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.db.session import get_session_factory

log = logging.getLogger(__name__)

_BASE_URL: Final = "https://fapi.binance.com"
_TIMEOUT: Final = 10.0

# Effectively unlimited: only used to wrap a caller-supplied httpx client
# (tests, standalone scripts) so the RateLimitedClient plumbing is shared
# by both code paths without ever throttling non-production callers.
_STANDALONE_BUCKET_CAPACITY: Final = 10_000.0

_NULL: Final[dict[str, float | None]] = {
    "ls_account_ratio": None,
    "taker_buy_sell_ratio": None,
    "oi_4h_delta": None,
    "oi_24h_delta": None,
}

_FLOW_CACHE: dict[str, dict[str, float | None]] = {}

# TIER 2a (defect sweep 2026-08-06): the taker-ratio and OI-delta wrong-
# endpoint incidents both looked identical from here — every call to one
# endpoint failing, silently, for months. A single symbol's fetch failing
# (delisted, thin market) is a legitimate quirk that must stay quiet; the
# SAME endpoint failing on every consecutive call is the exact shape those
# two incidents had and must now be loud well before a full universe cycle
# (~30 symbols) completes. Reset to 0 on any success for that endpoint.
_CONSECUTIVE_FAILURE_ALERT_THRESHOLD: Final = 20
_consecutive_failures: dict[str, int] = {
    "ls_account_ratio": 0, "taker_buy_sell_ratio": 0, "oi_delta": 0,
}


def _record_endpoint_result(endpoint: str, *, ok: bool, symbol: str) -> None:
    """Update the per-endpoint consecutive-failure streak; escalate once."""
    if ok:
        _consecutive_failures[endpoint] = 0
        return
    _consecutive_failures[endpoint] += 1
    streak = _consecutive_failures[endpoint]
    if streak >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
        log.error(
            "flow_features: %s has failed %d consecutive calls (latest "
            "symbol=%s) — this endpoint looks broken for every symbol, not "
            "a one-off per-symbol quirk. Same shape as the taker-ratio and "
            "OI-delta wrong-endpoint incidents (2026-08-06).",
            endpoint, streak, symbol,
        )


def _clear_failure_streaks_for_tests() -> None:
    for key in _consecutive_failures:
        _consecutive_failures[key] = 0


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


def _resolve_rate_client(http: httpx.AsyncClient | None) -> RateLimitedClient:
    """Route through the shared Binance Futures bucket, or wrap a caller's client.

    No explicit ``http=``: production default. Reuses the same
    ``RateLimitedClient`` singleton as the intermarket adapter, so this
    module's traffic is counted against the real shared weight limit
    instead of bypassing it.

    Explicit ``http=``: tests/standalone callers. Wrapped in a permissive
    bucket (effectively never blocks) purely so both paths share the same
    ``.request()`` call shape — the caller's own client/transport is used
    unchanged and remains caller-owned (not closed here).
    """
    if http is None:
        from app.data.adapters import get_intermarket_adapter
        rate_client = get_intermarket_adapter().rate_client
        assert rate_client is not None
        return rate_client
    return RateLimitedClient(
        exchange="binance_futures_flow_standalone",
        http=http,
        buckets={
            "default": TokenBucket(
                capacity=_STANDALONE_BUCKET_CAPACITY,
                refill_per_sec=_STANDALONE_BUCKET_CAPACITY,
            ),
        },
    )


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

    With no ``http=`` override (the production shadow-worker call site),
    requests go through the shared Binance Futures ``RateLimitedClient``
    singleton — see module docstring (FU-40). Pass ``http=`` to inject a
    client for tests/standalone use; it is never closed here (caller-owned).
    """
    native = symbol.replace("/", "")
    result: dict[str, float | None] = dict(_NULL)

    rate_client = _resolve_rate_client(http)
    try:
        # 1. Long/Short account ratio
        try:
            resp = await rate_client.request(
                "GET", f"{base_url}/futures/data/globalLongShortAccountRatio",
                endpoint_key="globalLongShortAccountRatio",
                params={"symbol": native, "period": "5m", "limit": "1"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                result["ls_account_ratio"] = float(rows[0]["longAccount"])
            _record_endpoint_result("ls_account_ratio", ok=True, symbol=native)
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features ls_account_ratio error %s: %s", native, exc)
            _record_endpoint_result("ls_account_ratio", ok=False, symbol=native)

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
            resp = await rate_client.request(
                "GET", f"{base_url}/futures/data/takerlongshortRatio",
                endpoint_key="takerlongshortRatio",
                params={"symbol": native, "period": "5m", "limit": "1"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                buy_vol = float(rows[0]["buyVol"])
                sell_vol = float(rows[0]["sellVol"])
                total = buy_vol + sell_vol
                if total > 0:
                    result["taker_buy_sell_ratio"] = buy_vol / total
            _record_endpoint_result("taker_buy_sell_ratio", ok=True, symbol=native)
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features taker_ratio error %s: %s", native, exc)
            _record_endpoint_result("taker_buy_sell_ratio", ok=False, symbol=native)

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
            resp = await rate_client.request(
                "GET", f"{base_url}/futures/data/openInterestHist",
                # Same endpoint_key the intermarket adapter registers for
                # this literal Binance path, so both modules' calls draw
                # from one coordinated weight count for it.
                endpoint_key="openInterestHist",
                params={"symbol": native, "period": "1h", "limit": "25"},
                timeout=_TIMEOUT,
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
            _record_endpoint_result("oi_delta", ok=True, symbol=native)
        except Exception as exc:  # noqa: BLE001
            log.debug("flow_features oi_delta error %s: %s", native, exc)
            _record_endpoint_result("oi_delta", ok=False, symbol=native)

    finally:
        _FLOW_CACHE[symbol] = result


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
