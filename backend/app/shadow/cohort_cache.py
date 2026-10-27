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
    fetch_market_listings_full,
    load_baseline_symbols,
)

log = logging.getLogger(__name__)

_baseline_cache: set[str] | None = None
_futures_only_cache: set[str] | None = None
_spot_listed_cache: set[str] | None = None
# ROUTING sets (2026-10-27): listed-at-all, minus never-traded
# statuses. DISTINCT from the tradable sets above because they answer
# a different question -- 'does this market have bars' rather than
# 'may I trade this'. A halted (BREAK) or settling (SETTLING) symbol
# is not tradable but its history still exists, and routing on the
# tradable predicate refused data that was right there.
_routable_futures_only_cache: set[str] | None = None
_routable_spot_cache: set[str] | None = None
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
    contract so callers can log/heartbeat off the return value directly).

    It populates the SPOT-listed cache in the same cycle (FU-53). Both
    sets come out of the same pair of exchangeInfo calls, so this costs
    no extra request, and they must be refreshed together or not at all:
    `app.data.bar_fetch` resolves an endpoint by requiring POSITIVE
    membership in one set or the other, and two sets from different
    moments could answer "neither" for a symbol that one of them knew
    about. The name still says futures_only because that is the cycle's
    purpose and its 24h cadence; the spot set is a by-product of the
    same two calls.
    """
    global _futures_only_cache, _spot_listed_cache, _futures_only_cache_loaded_at
    global _routable_futures_only_cache, _routable_spot_cache
    try:
        listings = await fetch_market_listings_full(http)
        futures_only = set(listings.tradable_futures_only)
        spot_listed = set(listings.tradable_spot)
        _futures_only_cache = futures_only
        _spot_listed_cache = spot_listed
        # Same cycle, same two HTTP calls. All FOUR sets must move
        # together or a routing lookup could consult a tradable set from
        # one moment against a routable set from another and answer
        # "neither" for a symbol one of them knew.
        _routable_futures_only_cache = set(listings.routable_futures_only)
        _routable_spot_cache = set(listings.routable_spot)
        _futures_only_cache_loaded_at = _utc_now()
        log.info(
            "cohort_cache: futures_only refreshed (%d tradable futures-only, "
            "%d tradable spot | %d routable futures-only, %d routable spot)",
            len(futures_only), len(spot_listed),
            len(_routable_futures_only_cache), len(_routable_spot_cache),
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


def get_spot_listed_cache() -> set[str] | None:
    """Sync, zero-I/O read of the USDT SPOT listing. None means the
    refresh has never succeeded since process start.

    This exists so a symbol's market can be established by POSITIVE
    evidence. Absence from `get_futures_only_cache()` is NOT that
    evidence -- it is also what a listing newer than the last refresh
    looks like -- so `app.data.bar_fetch` consults both and raises when
    neither answers. Deriving "spot" from the absence is the silent
    default FU-53 removes.
    """
    return _spot_listed_cache


def get_routable_futures_only_cache() -> set[str] | None:
    """Sync, zero-I/O read of the ROUTING futures-only set.

    "Listed on futures and not on spot", where listed means the market
    knows the symbol -- INCLUDING halted and settling ones, because they
    still have history. Used ONLY by `app.data.bar_fetch` to choose an
    endpoint. Cohort and fleet selection must keep using
    `get_futures_only_cache()`, which is TRADING-only: a settling
    contract is routable but must not be traded.
    """
    return _routable_futures_only_cache


def get_routable_spot_cache() -> set[str] | None:
    """Sync, zero-I/O read of the ROUTING spot set. See the sibling above."""
    return _routable_spot_cache


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
    global _baseline_cache, _futures_only_cache, _spot_listed_cache
    global _futures_only_cache_loaded_at
    global _routable_futures_only_cache, _routable_spot_cache
    _baseline_cache = None
    _futures_only_cache = None
    _spot_listed_cache = None
    _routable_futures_only_cache = None
    _routable_spot_cache = None
    _futures_only_cache_loaded_at = None


__all__ = [
    "load_baseline_cache_once",
    "get_baseline_cache",
    "refresh_futures_only_cache",
    "get_futures_only_cache",
    "get_spot_listed_cache",
    "get_futures_only_cache_age_seconds",
]
