"""Multi-timeframe (MTF) confluence — 6-TF SPOT REST vote (PR1).

Per spec PART A. In PR1, results are RECORD-ONLY: the aggregator
attaches mtf_agreement / mtf_dominant_tf / mtf_directions_json to
the prediction row, but does NOT use them in any gate. PR2 adds
the dispatcher gate.

Source: Binance SPOT REST /api/v3/klines — geoblock-safe from Hetzner.
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

_BASE_URL = "https://api.binance.com"


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
    """

    agreement: int | None
    dominant_tf: str | None
    directions: dict[str, int] = field(default_factory=dict)


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


async def _fetch_one_tf(
    http: httpx.AsyncClient,
    symbol: str,
    tf: str,
    *,
    cache_get: Any = _cache_get,
    cache_set: Any = _cache_set,
) -> list[list[Any]] | None:
    """Fetch klines for one TF; cache-hit returns immediately (no I/O).

    On HTTP failure or timeout, returns None — caller treats as a
    zero-vote degradation per the fail-open contract.
    Per-TF timeout: TF_FETCH_TIMEOUT_S (2.0s) — operator-locked.
    """
    cached = cache_get(symbol, tf)
    if cached is not None:
        return cached

    try:
        resp = await http.get(
            f"{_BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": tf, "limit": KLINE_LIMIT},
            timeout=TF_FETCH_TIMEOUT_S,
        )
        resp.raise_for_status()
        klines: list[list[Any]] = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        log.warning("mtf fetch %s/%s failed: %s", symbol, tf, exc)
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
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
    try:
        results = await asyncio.gather(
            *[_fetch_one_tf(http, symbol, tf) for tf in TIMEFRAMES],
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
    return MtfConfluence(
        agreement=agreement,
        dominant_tf=dominant,
        directions=votes_only,
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
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
    cached_count = 0
    try:
        for sym in symbols:
            elapsed = time.time() - start
            if elapsed >= deadline_seconds:
                log.info("mtf_prewarm: deadline reached at sym=%s", sym)
                break
            remaining = deadline_seconds - elapsed
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_fetch_one_tf(http, sym, tf) for tf in TIMEFRAMES],
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
    own_http = _http is None
    http = _http or httpx.AsyncClient(timeout=TF_FETCH_TIMEOUT_S)
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
            await asyncio.gather(
                *[_fetch_one_tf(http, sym, tf) for sym, tf in due],
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

    Watchdog flag: pending_heartbeat=True — single-shot by design; no
    heartbeat is expected; staleness check is skipped.
    """

    async def _runner() -> None:
        from app.shadow.universe import load_current_universe

        log.info("mtf_cache_prewarm_task: spawned")
        try:
            async with session_factory() as session:
                entries = await load_current_universe(session)
            symbols = [e.symbol for e in entries[:30]]
            await prewarm_cache(symbols, deadline_seconds=60.0)
        except Exception as exc:  # noqa: BLE001 — fail-open per spec
            log.warning("mtf_cache_prewarm_task: failed: %s", exc)

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
