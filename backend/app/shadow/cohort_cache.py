"""In-process caches feeding `_classify_cohort` at position-open time.

Item 0 (2026-08-30): synchronous cohort classification at open time is
only safe if it never makes a network call from the hot path. Two
inputs feed `_classify_cohort`, and they have genuinely different
staleness profiles -- conflating them was the mistake to avoid here
(operator ruling, 2026-08-30):

  - BASELINE (`cohort_baseline_symbols`, migration 0041): frozen. It
    cannot change without a migration, so it is loaded ONCE at process
    start and never re-read. There is no "refresh" for this one --
    freezing it was the entire point of the migration.
  - FUTURES_ONLY (which symbols are futures-listed but not
    spot-listed): a near-static Binance listing property, not fleet
    membership -- it changes only when Binance lists or delists a
    pair. Refreshed on a dedicated 24h background cadence
    (`app.workers.futures_only_refresh`), decoupled from both the
    6h `live_fleet_universe` refresh and the position-open path.

Both are plain module-level state, read synchronously (no I/O, no
lock -- single-writer per cache, GIL makes the set-swap atomic enough
for this use). `get_*_cache()` returns None when the cache has never
been successfully populated; callers MUST treat None as "cannot
classify" and follow the NO-DEFAULT-ON-FAILURE rule (see
`app.shadow.worker`'s position-open path) -- never substitute a
guessed cohort. A hardcoded fallback tag is exactly the defect this
whole item was built to stop repeating (TRUMPUSDT's fabricated
established_top20 lineage, see live_fleet_universe.py's module
docstring).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.shadow.live_fleet_universe import (
    fetch_futures_and_futures_only_symbols,
    load_baseline_symbols,
)

log = logging.getLogger(__name__)

_baseline_cache: set[str] | None = None
_futures_only_cache: set[str] | None = None
_futures_only_cache_loaded_at: datetime | None = None


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def load_baseline_cache_once(session: AsyncSession) -> set[str]:
    """Populate the baseline cache on first call; every subsequent call
    (from any worker, any position open) returns the already-cached set
    with zero I/O. Safe to call from `ShadowWorker.setup()` every
    startup -- idempotent, and a fresh process restart re-reading the
    73 frozen rows once is correct, not wasteful."""
    global _baseline_cache
    if _baseline_cache is not None:
        return _baseline_cache
    try:
        _baseline_cache = await load_baseline_symbols(session)
        log.info(
            "cohort_cache: baseline loaded (%d symbols)", len(_baseline_cache),
        )
    except Exception as e:  # noqa: BLE001
        log.error("cohort_cache: baseline load FAILED, cache stays empty: %s", e)
        raise
    return _baseline_cache


def get_baseline_cache() -> set[str] | None:
    """Sync, zero-I/O read. None means load_baseline_cache_once has
    never succeeded -- callers must not guess a cohort in that case."""
    return _baseline_cache


async def refresh_futures_only_cache(http: httpx.AsyncClient) -> set[str] | None:
    """One refresh cycle for the daily futures_only cache. On failure,
    the PRIOR cached value (if any) is left in place -- a stale-but-real
    cache from yesterday is safer than wiping it to None on a transient
    Binance hiccup, which would turn every position open for the next
    24h into a forced NULL until the next successful cycle. Returns the
    new set on success, None on failure (mirroring get_futures_only_cache's
    contract so callers can log/heartbeat off the return value directly)."""
    global _futures_only_cache, _futures_only_cache_loaded_at
    try:
        _, futures_only = await fetch_futures_and_futures_only_symbols(http)
        _futures_only_cache = futures_only
        _futures_only_cache_loaded_at = _utc_now()
        log.info(
            "cohort_cache: futures_only refreshed (%d symbols)", len(futures_only),
        )
        return futures_only
    except Exception as e:  # noqa: BLE001
        log.error(
            "cohort_cache: futures_only refresh FAILED -- keeping prior cache "
            "(loaded_at=%s, %d symbols): %s",
            _futures_only_cache_loaded_at,
            len(_futures_only_cache) if _futures_only_cache is not None else 0,
            e,
        )
        return None


def get_futures_only_cache() -> set[str] | None:
    """Sync, zero-I/O read. None means refresh_futures_only_cache has
    never succeeded even once since process start -- callers must not
    guess a cohort in that case."""
    return _futures_only_cache


def get_futures_only_cache_age_seconds() -> float | None:
    """None if never loaded; otherwise seconds since the last successful
    refresh. Used by the healer/heartbeat to flag a cache that's gone
    silently stale (refresh loop wedged, not just a single failed cycle
    -- refresh_futures_only_cache already logs those individually)."""
    if _futures_only_cache_loaded_at is None:
        return None
    return (_utc_now() - _futures_only_cache_loaded_at).total_seconds()


def _reset_for_tests() -> None:
    """Test-only: clear all module state between tests. Never called
    from production code."""
    global _baseline_cache, _futures_only_cache, _futures_only_cache_loaded_at
    _baseline_cache = None
    _futures_only_cache = None
    _futures_only_cache_loaded_at = None


__all__ = [
    "load_baseline_cache_once",
    "get_baseline_cache",
    "refresh_futures_only_cache",
    "get_futures_only_cache",
    "get_futures_only_cache_age_seconds",
]
