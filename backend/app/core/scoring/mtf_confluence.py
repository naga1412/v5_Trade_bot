"""Multi-timeframe (MTF) confluence — 6-TF SPOT REST vote (PR1).

Per spec PART A. In PR1, results are RECORD-ONLY: the aggregator
attaches mtf_agreement / mtf_dominant_tf / mtf_directions_json to
the prediction row, but does NOT use them in any gate. PR2 adds
the dispatcher gate.

Source: Binance klines REST, endpoint chosen per symbol by
app.data.bar_fetch.resolve_klines_url_for_symbol — spot for
spot-listed pairs (geoblock-safe from Hetzner), fapi for
futures-only contracts. Defect (a), migrated 2026-10-29.
Cache: module-level dict, TTL per TF tier (5m=60s, 15m=60s, 1h=300s,
       4h/1d/1w=3600s).
Concurrency: asyncio.gather(return_exceptions=True) — any per-TF
             timeout or HTTP failure degrades that TF only.

Module structure:
  Section 1: Constants + dataclass + cache primitives + EMA/ADX + _vote_for_tf
  Section 2: _fetch_one_tf + _compute_agreement_and_dominant + compute_mtf_confluence
  Section 3: prewarm_cache + _entries_due_for_refresh + run_mtf_cache_refresh_loop
  Section 4: start_mtf_cache_prewarm_task + start_mtf_cache_ttl_refresh_task factories
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from app.core.scoring.types import Direction


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section 1: Constants + dataclass + cache primitives + EMA/ADX + _vote_for_tf
# ---------------------------------------------------------------------------

TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d", "1w")

# Per-TF cache TTLs (seconds). Operator-locked: 5m=60s, 15m=60s, 1h=300s,
# 4h=3600s, 1d=3600s, 1w=3600s. No Redis — module-level dict only.
CACHE_TTL_S: dict[str, int] = {
    "5m": 60,
    "15m": 60,
    "1h": 300,
    "4h": 3600,
    "1d": 3600,
    "1w": 3600,
}

KLINE_LIMIT: int = 200
TF_FETCH_TIMEOUT_S: float = 2.0  # per-TF timeout; None on timeout (fail-open)
ADX_PERIOD: int = 14
EMA_SHORT: int = 20
EMA_LONG: int = 50
ADX_TREND_FLOOR: float = 20.0  # ADX < floor → no trending signal → vote=0

# NO MODULE-LEVEL BASE URL ANY MORE. Defect (a), migrated 2026-10-29.
# The endpoint is a per-SYMBOL fact, so a module constant could only
# ever be a default -- and the default was the defect. It is resolved
# once per symbol by `_resolve_symbol_url` below and passed down.

# --- defect (b): TRANSPORT. Fixed 2026-10-27, independent of defect (a) ---
#
# This module used `httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)` PER CALL
# and fired 6 concurrent requests with no limiter, no bucket, no weight
# accounting and no semaphore -- while flow_features, binance,
# binance_futures_intermarket and bybit all use the shared
# RateLimitedClient. It was the unthrottled outlier, and
# KNOWN_ISSUES.md:185 already recorded the per-call construction.
#
# WHY THAT IS A HAZARD ON ITS OWN TERMS, regardless of (a): a fresh client
# per call never reuses a connection, so every scoring cycle opens and
# discards one socket per request. TIME_WAIT / FD accumulation then
# produces TRANSPORT failures on ARBITRARY symbols -- intermittently,
# environment-specifically, and NOT reproducible from a clean machine.
# That is the leading explanation for ARBUSDT, which is demonstrably
# healthy: 200 on all six timeframes in 118-189 ms single, and `ok` inside
# a 180-request production-scale burst, yet the prod counter classed it
# PERMANENT across two cycles.
#
# ENDPOINT SELECTION IS DELIBERATELY UNTOUCHED. Defect (a) -- spot URL for
# futures-only symbols -- is held pending the status-code split. Fixing
# the transport does not require fixing the routing, and bundling them
# would make a narrow operational fix into a scoring change.
_SHARED_POOL_MAX_CONNECTIONS = 20
_SHARED_POOL_MAX_KEEPALIVE = 10

# KEYED BY EVENT LOOP, not by process. An httpx.AsyncClient keeps
# connections bound to the loop that opened them, so reusing one client
# across loops raises `RuntimeError: Event loop is closed` on the second
# loop -- verified directly, not assumed.
#
# Production has a single loop for the process's life, so a per-process
# singleton would have been correct there and WRONG in the test suite:
# pytest-asyncio is function-scoped here
# (`asyncio_default_fixture_loop_scope = "function"`), and
# tests/unit/test_predictor.py's network-marked tests each call
# build_prediction -> compute_mtf_confluence with no injected client. The
# first would have bound the pool to its loop and the remaining fourteen
# would have failed on theirs.
#
# A WeakKeyDictionary rather than `id(loop)`: an id can be recycled after
# a loop is collected, which would hand back a client bound to a dead
# loop -- the same bug with a harder-to-read traceback. Entries vanish
# with their loop.
_rate_clients: "weakref.WeakKeyDictionary[Any, Any]" = (
    weakref.WeakKeyDictionary()
)


def get_rate_client() -> Any:
    """The pooled, weight-limited client for MTF klines, per event loop.

    One per loop, built on first use and never closed: closing it after a
    compute call would reintroduce exactly the per-call socket churn this
    exists to remove. Bucket parameters mirror
    `app.data.adapters.binance._default_rate_client` so the two account
    against Binance's documented per-IP budget shape rather than
    inventing a second one.

    Requires a running loop. That is deliberate -- a client fetched
    without one could only be used on a loop it was not built for, which
    is the failure this keying prevents.
    """
    from app.data.ratelimit import RateLimitedClient, TokenBucket

    loop = asyncio.get_running_loop()
    client = _rate_clients.get(loop)
    if client is None:
        client = RateLimitedClient(
            exchange="binance",
            http=httpx.AsyncClient(
                timeout=TF_FETCH_TIMEOUT_S,
                limits=httpx.Limits(
                    max_connections=_SHARED_POOL_MAX_CONNECTIONS,
                    max_keepalive_connections=_SHARED_POOL_MAX_KEEPALIVE,
                ),
            ),
            buckets={"default": TokenBucket(capacity=1200, refill_per_sec=20.0)},
            endpoint_weights={"klines": 2},
            sync_header="X-MBX-USED-WEIGHT-1M",
            sync_capacity=1200.0,
        )
        _rate_clients[loop] = client
    return client


def _reset_rate_client_for_tests() -> None:
    _rate_clients.clear()


async def _get_klines(client: Any, url: str, *, params: dict, timeout: float):
    """One GET, through either a RateLimitedClient or a bare AsyncClient.

    The bare-client path is kept because the existing tests inject an
    `httpx.AsyncClient` with a mock transport directly into
    `_fetch_one_tf`, and rewriting that seam would have been a far larger
    diff than the fix. Dispatch is on TYPE, not on a capability sniff, so
    a future client that happens to expose `.request` cannot silently
    take the unthrottled path.
    """
    from app.data.ratelimit import RateLimitedClient

    if isinstance(client, RateLimitedClient):
        return await client.request(
            "GET", url, endpoint_key="klines", params=params, timeout=timeout,
        )
    return await client.get(url, params=params, timeout=timeout)


@dataclass(frozen=True)
class MtfConfluence:
    """Result of a multi-timeframe vote.

    Per Correction 4:
      agreement:   count of TFs voting same direction as signal (LONG/SHORT);
                   None for NEUTRAL signals (avoids "agreement with what?" ambiguity).
      dominant_tf: TF with highest |vote × ADX| across all 6; None if all
                   six TFs voted 0 (no trending TF).
      directions:  per-TF vote map { "5m": +1, "15m": -1, "1h": 0, ... } —
                   always populated fully even for NEUTRAL signals.
      adx_by_tf:   per-TF ADX(14) magnitude {"5m": 18.3, "1h": 31.2, ...}.
                   Computed alongside the EMA cross vote; surfaced so the
                   entry-quality gate can apply a global ADX threshold on
                   the dominant TF without re-fetching klines.
                   PR-BOT-INTELLIGENCE-UPGRADE CHANGE 3.
    """

    agreement: int | None
    dominant_tf: str | None
    directions: dict[str, int] = field(default_factory=dict)
    adx_by_tf: dict[str, float] = field(default_factory=dict)


@dataclass
class _CacheEntry:
    klines: list[list[Any]]
    fetched_at: float


# Module-level dict: (symbol, tf) → _CacheEntry. No Redis, no stampede protection.
_KLINE_CACHE: dict[tuple[str, str], _CacheEntry] = {}


def _cache_get(
    symbol: str,
    tf: str,
    *,
    now: float | None = None,
) -> list[list[Any]] | None:
    """Return cached klines if within TTL, else None."""
    n = now if now is not None else time.time()
    entry = _KLINE_CACHE.get((symbol, tf))
    if entry is None:
        return None
    if (n - entry.fetched_at) >= CACHE_TTL_S[tf]:
        return None
    return entry.klines


def _cache_set(
    symbol: str,
    tf: str,
    klines: list[list[Any]],
    *,
    fetched_at: float | None = None,
) -> None:
    """Store klines in cache with optional injected timestamp (for tests)."""
    _KLINE_CACHE[(symbol, tf)] = _CacheEntry(
        klines=klines,
        fetched_at=fetched_at if fetched_at is not None else time.time(),
    )


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average — simple iterative, no talib dependency.

    Uses full-history initialization: first value = arr[0], then EMA update.
    This avoids NaN warm-up issues for the short arrays common in tests.
    """
    alpha = 2.0 / (period + 1)
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = float(arr[0])
    for i in range(1, len(arr)):
        out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i - 1]
    return out


def _adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = ADX_PERIOD,
) -> float:
    """Compute ADX(period) — Wilder-smoothed directional index.

    Returns the final ADX value as a float. Returns 0.0 when insufficient
    data (< period + 2 bars).
    """
    n = len(close)
    if n < period + 2:
        return 0.0

    # True Range, +DM, -DM (shift by 1)
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])
    dm_plus = np.where(
        (high[1:] - high[:-1]) > (low[:-1] - low[1:]),
        np.maximum(high[1:] - high[:-1], 0.0),
        0.0,
    )
    dm_minus = np.where(
        (low[:-1] - low[1:]) > (high[1:] - high[:-1]),
        np.maximum(low[:-1] - low[1:], 0.0),
        0.0,
    )

    # Wilder-smoothed ATR, +DI, -DI
    atr = _ema(tr, period)
    safe_atr = np.where(atr == 0, 1.0, atr)
    di_plus = 100.0 * _ema(dm_plus, period) / safe_atr
    di_minus = 100.0 * _ema(dm_minus, period) / safe_atr

    # DX → ADX
    denom = np.where((di_plus + di_minus) == 0, 1.0, di_plus + di_minus)
    dx = 100.0 * np.abs(di_plus - di_minus) / denom
    return float(_ema(dx, period)[-1])


def _vote_for_tf(klines: list[list[Any]]) -> tuple[int, float]:
    """Compute +1/-1/0 vote + ADX(14) magnitude from EMA20/50 cross.

    Returns ``(vote, adx)`` — ADX is returned even when vote=0 so that
    ``_compute_agreement_and_dominant`` can rank TFs by ``|vote × ADX|``
    per Correction 4 (dominant_tf semantics).

    Binance kline row format: [open_time, o, h, l, c, vol, close_time, ...].
    Index 2=high, 3=low, 4=close.
    """
    if len(klines) < max(EMA_LONG, ADX_PERIOD + 2):
        return 0, 0.0

    closes = np.asarray([float(k[4]) for k in klines], dtype=np.float64)
    highs = np.asarray([float(k[2]) for k in klines], dtype=np.float64)
    lows = np.asarray([float(k[3]) for k in klines], dtype=np.float64)

    ema_short_val = _ema(closes, EMA_SHORT)[-1]
    ema_long_val = _ema(closes, EMA_LONG)[-1]
    adx_val = _adx(highs, lows, closes)

    if adx_val < ADX_TREND_FLOOR:
        # Choppy market — no directional signal regardless of EMA cross
        return 0, adx_val

    if ema_short_val > ema_long_val:
        return +1, adx_val
    if ema_short_val < ema_long_val:
        return -1, adx_val
    return 0, adx_val


# ---------------------------------------------------------------------------
# Section 2: _fetch_one_tf + _compute_agreement_and_dominant + compute_mtf_confluence
# ---------------------------------------------------------------------------


# --- fail-open observability (2026-10-26) ---------------------------------
# A counter, not just a log line. This repo has shipped log-only
# instrumentation repeatedly and then had no retrieval path when the
# question came up (FU-47's class), so the count is readable in-process by
# a probe or heartbeat without grepping a container's log buffer -- which is
# truncated on every redeploy anyway.
_fetch_failures: dict[str, int] = {}

# THE SECOND KEY, added 2026-10-26. The symbol-level counter above
# ACCEPTED `tf` AND DISCARDED IT, so "which timeframes" and "which
# failure" -- the only two facts that separate a routing failure from a
# timeout -- existed at the call site and were thrown away. Classifying
# ARBUSDT as permanent-vs-transient took two manual reads of consecutive
# hourly cycles because of it.
#
# Keyed (symbol, timeframe, failure) so the shape is readable directly:
#   routing failure -> every timeframe fails, equally and persistently
#   timeout         -> concentrates on the longest fetches, or scatters
#
# Bounded by symbols x 6 timeframes x failure kinds, aggregated daily, so
# it stays a few hundred rows a day rather than per-event amplification.
# The symbol-level series is KEPT UNCHANGED beside it: the 2026-10-26
# permanent/transient classification was made from that series and
# re-keying it would have broken the comparison.
_fetch_failure_detail: dict[tuple[str, str, str], int] = {}


def _failure_kind(exc: BaseException | None) -> str:
    """A short, groupable label for the failure.

    The HTTP STATUS IS PART OF THE LABEL because that is the whole
    discriminator: 400/404 means Binance does not have this symbol on
    this market (routing), while a timeout or 429/418 means the request
    never got a verdict (transport). Collapsing them to
    "HTTPStatusError" would throw away the answer a second time.
    """
    if exc is None:
        return "unknown"
    name = type(exc).__name__
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if code is not None:
        return f"{name}_{code}"
    return name


def _record_fetch_failure(
    symbol: str, tf: str, exc: BaseException | None = None,
) -> None:
    """Count one failed per-timeframe fetch.

    `exc` is optional so the existing two-argument call shape stays
    valid; passing it is what makes the detail counter useful.
    """
    _fetch_failures[symbol] = _fetch_failures.get(symbol, 0) + 1
    key = (symbol, tf, _failure_kind(exc))
    _fetch_failure_detail[key] = _fetch_failure_detail.get(key, 0) + 1


def get_fetch_failure_detail() -> dict[tuple[str, str, str], int]:
    """Per-(symbol, timeframe, failure) counts since process start.

    Deliberately a SEPARATE accessor rather than a widened return from
    :func:`get_fetch_failure_counts`: that function's `dict[str, int]`
    shape is consumed by the flusher and its tests, and widening a
    return in place is the pitfall that broke three integration unpack
    sites when PR #266 bumped a tuple from 8 to 9.
    """
    return dict(_fetch_failure_detail)


def get_fetch_failure_counts() -> dict[str, int]:
    """Per-symbol MTF kline-fetch failures since process start.

    A symbol appearing here at all means its MTF vote has been degraded to
    zero-agreement at least once; a count that tracks the number of scoring
    cycles means it is degraded permanently, not intermittently.
    """
    return dict(_fetch_failures)


def _reset_fetch_failures_for_tests() -> None:
    _fetch_failures.clear()
    _fetch_failure_detail.clear()


def _resolve_symbol_url(symbol: str, tfs: tuple[str, ...] = TIMEFRAMES) -> str | None:
    """The klines endpoint for *symbol*, or None having COUNTED the failure.

    DEFECT (a), MIGRATED 2026-10-29. This module used to build
    `https://api.binance.com/api/v3/klines` for every symbol it scored.
    For a futures-only contract that request can only ever 400: the
    prod counter recorded 3,387 of them over 47 cycles, 96.50% of all
    MTF fetch failures, across 23 symbols failing 6 of 6 timeframes.
    Each one became a zero-agreement vote through the fail-open path
    below, indistinguishable from genuine timeframe disagreement.

    ON THE 1000x NAMING, because the next reader will otherwise add a
    mapping that must not exist. Binance lists some perpetuals at 1000x
    the underlying: the CONTRACT is `1000PEPEUSDT` while the SPOT PAIR
    is `PEPEUSDT`. So for `1000PEPEUSDT`, "has no spot pair" is true OF
    THE CONTRACT STRING and false of the asset -- `PEPEUSDT` is listed
    and trading.

    THAT IS THE CORRECT BEHAVIOUR AND NEEDS NO NAME MAPPING. We hold a
    position in the `1000PEPEUSDT` perpetual; its price, its bars and
    its ADX are the contract's, not the asset's. Routing it to fapi is
    right. Rewriting the symbol to `PEPEUSDT` to reach spot would fetch
    bars for a DIFFERENT, 1000x-rescaled instrument -- numerically
    offset, and on a market we are not trading. Do not add a mapping.

    Returns None rather than raising, because the whole fetch path here
    is fail-open by an operator-locked contract (see `_fetch_one_tf`).
    But it returns None *having recorded one failure per timeframe*
    under the kind `UnresolvedMarket`, so an unplaceable symbol is
    visible in the same counter series as a 400 or a timeout. Silent
    fail-open is the defect regardless of which value is correct.
    """
    from app.data.bar_fetch import UnresolvedMarket, resolve_klines_url_for_symbol

    try:
        return resolve_klines_url_for_symbol(symbol.replace("/", ""))
    except UnresolvedMarket as exc:
        for tf in tfs:
            _record_fetch_failure(symbol, tf, exc)
        # Not an error: the two reachable causes are a cache that has not
        # completed its first refresh since process start (transient, and
        # the refresher fires one cycle immediately on boot), and a symbol
        # listed since the last 24h refresh. Neither is a code defect, and
        # both are already counted above.
        log.warning(
            "mtf_resolve_failed: symbol=%s -- no endpoint from positive "
            "listing membership, scoring %d timeframes as zero-agreement. "
            "NOT defaulting to spot: that default is defect (a). %s",
            symbol, len(tfs), exc,
        )
        return None


async def _fetch_one_tf(
    http: httpx.AsyncClient,
    symbol: str,
    tf: str,
    *,
    url: str,
    cache_get: Any = _cache_get,
    cache_set: Any = _cache_set,
) -> list[list[Any]] | None:
    """Fetch klines for one TF; cache-hit returns immediately (no I/O).

    On HTTP failure or timeout, returns None — caller treats as a
    zero-vote degradation per the fail-open contract.
    Per-TF timeout: TF_FETCH_TIMEOUT_S (2.0s) — operator-locked.

    `url` IS REQUIRED AND KEYWORD-ONLY, with no default. A default would
    be a spot URL, which is defect (a) re-entering through its own fix --
    the same shape `bar_fetch.resolve_klines_url_for_symbol` refuses an
    `else` branch for. Resolving it ONCE per symbol in the three callers,
    rather than per timeframe here, also makes the endpoint decision
    structurally per-symbol: all six timeframes of one symbol cannot
    disagree about which market they came from.
    """
    cached = cache_get(symbol, tf)
    if cached is not None:
        return cached

    try:
        resp = await _get_klines(
            http,
            url,
            params={"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT},
            timeout=TF_FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        klines: list[list[Any]] = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _record_fetch_failure(symbol, tf, exc)
        # ERROR, not WARNING, and counted. This is a FAIL-OPEN path: the
        # caller turns None into a zero-vote degradation, so a symbol whose
        # fetch always fails scores as though no higher timeframe agreed
        # with it -- indistinguishable from genuine disagreement. For the
        # futures_poll cohort that is not intermittent, it is permanent:
        # those symbols have no spot pair, so this spot URL can never
        # return bars for them (FU-53, same class as #539).
        #
        # Fail-open-silently is the defect regardless of what the correct
        # value turns out to be, which is why the severity and the counter
        # ship now while the routing fix waits to be measured.
        log.error(
            "mtf_kline_fetch_failed: symbol=%s tf=%s failures_for_symbol=%d "
            "-- FAIL-OPEN, this symbol scores as zero-agreement for this tf: "
            "%s",
            symbol, tf, _fetch_failures.get(symbol, 0), exc,
        )
        return None

    cache_set(symbol, tf, klines)
    return klines


def _compute_agreement_and_dominant(
    direction: Direction,
    tf_data: dict[str, tuple[int, float]],
) -> tuple[int | None, str | None]:
    """Reduce per-TF (vote, adx) data to (agreement, dominant_tf).

    Per Correction 4:
      - LONG/SHORT signal: agreement = count of TFs matching direction
      - NEUTRAL signal:    agreement = None (no direction to compare against)
      - dominant_tf:       TF with highest |vote × adx| across all 6;
                           None if all 6 TFs voted 0.
    """
    count_long = sum(1 for v, _ in tf_data.values() if v > 0)
    count_short = sum(1 for v, _ in tf_data.values() if v < 0)

    # Dominant TF: highest |vote × adx| among non-zero-voting TFs
    candidates: list[tuple[str, float]] = [
        (tf, abs(v) * adx)
        for tf, (v, adx) in tf_data.items()
        if v != 0
    ]
    dominant: str | None = (
        max(candidates, key=lambda x: x[1])[0] if candidates else None
    )

    agreement: int | None
    if direction is Direction.LONG:
        agreement = count_long
    elif direction is Direction.SHORT:
        agreement = count_short
    else:  # NEUTRAL — per Correction 4: no meaningful "agreement" count
        agreement = None

    return agreement, dominant


async def compute_mtf_confluence(
    symbol: str,
    signal_direction: Direction,
    *,
    _http: httpx.AsyncClient | None = None,
) -> MtfConfluence | None:
    """Top-level: fetch + vote across all 6 TFs in parallel via asyncio.gather.

    Operator-locked concurrency contract:
      - asyncio.gather(return_exceptions=True) — no per-TF crash propagates
      - Each per-TF failure (exception OR None) → (0, 0.0) vote
      - Returns None ONLY if ALL 6 TFs failed
      - Otherwise returns a partial result (failed TFs recorded as 0 votes)
    """
    # Never closed: the shared client is process-wide, and an injected
    # one belongs to the caller. Closing either is what produced the
    # per-call socket churn.
    own_http = False
    http = _http if _http is not None else get_rate_client()

    # Resolved ONCE for all six timeframes. On failure the symbol is
    # already counted per timeframe, and returning None here is the same
    # answer the all-six-failed branch below gives -- reached without
    # issuing six requests that cannot succeed.
    url = _resolve_symbol_url(symbol)
    if url is None:
        return None

    try:
        results = await asyncio.gather(
            *[_fetch_one_tf(http, symbol, tf, url=url) for tf in TIMEFRAMES],
            return_exceptions=True,
        )
    finally:
        if own_http:
            await http.aclose()

    tf_data: dict[str, tuple[int, float]] = {}
    failures = 0
    for tf, klines_or_exc in zip(TIMEFRAMES, results, strict=True):
        if isinstance(klines_or_exc, BaseException) or klines_or_exc is None:
            tf_data[tf] = (0, 0.0)
            failures += 1
            continue
        tf_data[tf] = _vote_for_tf(klines_or_exc)

    if failures == len(TIMEFRAMES):
        log.warning("mtf_confluence: all 6 TFs failed for %s — returning None", symbol)
        return None

    agreement, dominant = _compute_agreement_and_dominant(signal_direction, tf_data)
    votes_only: dict[str, int] = {tf: v for tf, (v, _) in tf_data.items()}
    adx_only: dict[str, float] = {tf: adx for tf, (_, adx) in tf_data.items()}
    return MtfConfluence(
        agreement=agreement,
        dominant_tf=dominant,
        directions=votes_only,
        adx_by_tf=adx_only,
    )


# ---------------------------------------------------------------------------
# Section 3: prewarm_cache + _entries_due_for_refresh + run_mtf_cache_refresh_loop
# ---------------------------------------------------------------------------


async def prewarm_cache(
    symbols: list[str],
    *,
    deadline_seconds: float = 60.0,
    _http: httpx.AsyncClient | None = None,
) -> int:
    """Populate cache for symbols × TIMEFRAMES. Stops at deadline (fail-open).

    Returns count of (symbol, tf) entries successfully cached.

    The per-symbol gather is wrapped with asyncio.wait_for using the
    remaining deadline so that a slow batch cannot exceed the overall
    deadline (60s hard limit at startup).

    Logs:
      'mtf_prewarm: start symbols=N tfs=6 deadline=60.0s'
      'mtf_prewarm: done duration=X.XXs entries=N'
      OR 'mtf_prewarm: deadline reached at sym=X' (early exit)
    """
    start = time.time()
    log.info(
        "mtf_prewarm: start symbols=%d tfs=%d deadline=%.1fs",
        len(symbols),
        len(TIMEFRAMES),
        deadline_seconds,
    )
    # Never closed: the shared client is process-wide, and an injected
    # one belongs to the caller. Closing either is what produced the
    # per-call socket churn.
    own_http = False
    http = _http if _http is not None else get_rate_client()
    cached_count = 0
    try:
        for sym in symbols:
            elapsed = time.time() - start
            if elapsed >= deadline_seconds:
                log.info("mtf_prewarm: deadline reached at sym=%s", sym)
                break
            remaining = deadline_seconds - elapsed
            sym_url = _resolve_symbol_url(sym)
            if sym_url is None:
                # Counted inside the resolver. `continue`, not `break`:
                # one unplaceable symbol must not end the prewarm sweep
                # for every symbol after it.
                continue
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_fetch_one_tf(http, sym, tf, url=sym_url)
                          for tf in TIMEFRAMES],
                        return_exceptions=True,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                log.info("mtf_prewarm: deadline reached at sym=%s", sym)
                break
            for tf, k in zip(TIMEFRAMES, results):
                if isinstance(k, BaseException) or k is None:
                    continue
                cached_count += 1
    finally:
        if own_http:
            await http.aclose()

    duration = time.time() - start
    log.info("mtf_prewarm: done duration=%.2fs entries=%d", duration, cached_count)
    return cached_count


def _entries_due_for_refresh(
    *,
    now: float | None = None,
    expiry_threshold_pct: float = 0.20,
) -> list[tuple[str, str]]:
    """Find cache entries within ``expiry_threshold_pct`` of their TTL expiry.

    An entry is "due" when:
      0 < (ttl - age) <= ttl × expiry_threshold_pct

    Entries past expiry (age >= ttl) are not returned — they'll be fetched
    cold on next request. No stampede protection per operator spec.
    """
    n = now if now is not None else time.time()
    due: list[tuple[str, str]] = []
    for (symbol, tf), entry in _KLINE_CACHE.items():
        age = n - entry.fetched_at
        ttl = CACHE_TTL_S[tf]
        time_to_expiry = ttl - age
        if 0 < time_to_expiry <= ttl * expiry_threshold_pct:
            due.append((symbol, tf))
    return due


async def run_mtf_cache_refresh_loop(
    session_factory: Any,  # async_sessionmaker[AsyncSession] — typed loosely for DI
    *,
    interval_s: int = 30,
    expiry_threshold_pct: float = 0.20,
    _http: httpx.AsyncClient | None = None,
) -> None:
    """Background loop: every interval_s seconds, refresh entries near expiry.

    Operator-locked behaviour (PR1):
      - record_heartbeat on every iteration (watchdog visibility)
      - sleep(interval_s), then scan + refresh
      - NO stampede protection, NO request coalescing (spec-locked)
      - Refresh failures: entry expires naturally; next read pays cold cost
    """
    from app.ops.heartbeat import record_heartbeat

    log.info("mtf_cache_refresh: starting (interval=%ds)", interval_s)
    # Never closed: the shared client is process-wide, and an injected
    # one belongs to the caller. Closing either is what produced the
    # per-call socket churn.
    own_http = False
    http = _http if _http is not None else get_rate_client()
    try:
        while True:
            await record_heartbeat(
                session_factory,
                WORKER_NAME_REFRESH,
                status="ok",
                details={"cache_entries": len(_KLINE_CACHE)},
            )
            await asyncio.sleep(interval_s)
            due = _entries_due_for_refresh(expiry_threshold_pct=expiry_threshold_pct)
            if not due:
                continue
            log.info("mtf_cache_refresh: tick — refreshing %d entries", len(due))
            # `due` is (symbol, timeframe) pairs, so resolve per DISTINCT
            # symbol and reuse. Resolving per pair would count an
            # unplaceable symbol once per due timeframe AND once per
            # timeframe inside the resolver, inflating the counter by up
            # to 6x for a symbol that is merely new.
            urls = {
                sym: _resolve_symbol_url(sym)
                for sym in sorted({sym for sym, _ in due})
            }
            await asyncio.gather(
                *[_fetch_one_tf(http, sym, tf, url=urls[sym])
                  for sym, tf in due if urls[sym] is not None],
                return_exceptions=True,
            )
    finally:
        if own_http:
            await http.aclose()


# ---------------------------------------------------------------------------
# Section 4: Worker names + factory functions
# ---------------------------------------------------------------------------

# Worker names used by worker_registry.py (per Correction 2 — must be
# registered as proper supervised workers, not orphan tasks).
WORKER_NAME_PREWARM: str = "mtf_cache_prewarm_task"
WORKER_NAME_REFRESH: str = "mtf_cache_ttl_refresh_task"


def start_mtf_cache_prewarm_task(session_factory: Any) -> "asyncio.Task[None]":
    """Spawn the single-shot prewarm as an asyncio.Task.

    Loads the current universe (top-30 by volume), runs prewarm_cache
    with a 60s hard deadline, then completes naturally.

    FU-1 H9 + FU-15: on clean exit the runner records ONE heartbeat with
    status="single_shot_completed". The watchdog (see
    app.ops.worker_watchdog) classifies the worker as
    `single_shot_completed` (non-alarming) instead of `no_signal`.

    The registry entry sets ``single_shot=True`` + ``liveness_query=HEARTBEAT``
    so the watchdog reads beat_at + last_status from worker_heartbeats.
    """

    async def _runner() -> None:
        from app.ops.heartbeat import record_heartbeat
        from app.shadow.universe import load_current_universe

        log.info("mtf_cache_prewarm_task: spawned")
        start_ts = time.time()
        cached_count = 0
        try:
            async with session_factory() as session:
                entries = await load_current_universe(session)
            symbols = [e.symbol for e in entries[:30]]
            cached_count = await prewarm_cache(symbols, deadline_seconds=60.0)
            duration = time.time() - start_ts
            # FU-1 H9: single heartbeat with the FU-15 marker status.
            await record_heartbeat(
                session_factory, WORKER_NAME_PREWARM,
                status="single_shot_completed",
                details={
                    "prewarmed_count": cached_count,
                    "duration_s": round(duration, 2),
                    "symbols_attempted": len(symbols),
                },
            )
        except Exception as exc:  # noqa: BLE001 — fail-open per spec
            log.warning("mtf_cache_prewarm_task: failed: %s", exc)
            # Record an error heartbeat so the watchdog reports
            # `single_shot_failed` instead of `single_shot_never_completed`.
            try:
                await record_heartbeat(
                    session_factory, WORKER_NAME_PREWARM,
                    status="error",
                    details={"error": str(exc)[:200]},
                )
            except Exception:  # noqa: BLE001 — best-effort
                pass

    return asyncio.create_task(_runner(), name=WORKER_NAME_PREWARM)


def start_mtf_cache_ttl_refresh_task(session_factory: Any) -> "asyncio.Task[None]":
    """Spawn the long-running TTL-refresh loop as an asyncio.Task.

    Runs indefinitely; cancelled cleanly by the lifespan teardown.
    Heartbeats every 30s so the worker_watchdog can verify liveness.
    """
    log.info("mtf_cache_ttl_refresh_task: spawning")
    return asyncio.create_task(
        run_mtf_cache_refresh_loop(session_factory, interval_s=30),
        name=WORKER_NAME_REFRESH,
    )


__all__ = [
    # Constants
    "ADX_PERIOD",
    "ADX_TREND_FLOOR",
    "CACHE_TTL_S",
    "EMA_LONG",
    "EMA_SHORT",
    "KLINE_LIMIT",
    "TIMEFRAMES",
    "TF_FETCH_TIMEOUT_S",
    "WORKER_NAME_PREWARM",
    "WORKER_NAME_REFRESH",
    # Types
    "MtfConfluence",
    # Cache primitives (exposed for tests)
    "_cache_get",
    "_cache_set",
    "_KLINE_CACHE",
    # Vote helpers
    "_vote_for_tf",
    # Fetch + compute
    "_fetch_one_tf",
    "compute_mtf_confluence",
    # Prewarm + refresh
    "_entries_due_for_refresh",
    "prewarm_cache",
    "run_mtf_cache_refresh_loop",
    # Worker factories
    "start_mtf_cache_prewarm_task",
    "start_mtf_cache_ttl_refresh_task",
]
