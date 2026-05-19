"""PR10 symbol allowlist gate — dispatcher pre-condition.

Reads latest snapshots, applies stablecoin filter + Sharpe rule.
Two distinct outcomes: blocked_stablecoin / blocked_low_sharpe.

Fail-open contract: any DB error returns None (let trade proceed).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.symbol_performance_snapshots import (
    SymbolSnapshot,
    load_latest_snapshots_per_symbol,
)
from app.trading.symbol_allowlist import (
    _AllowlistCache,
    is_stablecoin_pair,
    is_symbol_allowed,
)


log = logging.getLogger(__name__)


# Process-local cache per (user_id,). One asyncio.Lock per user to
# serialize cache rebuilds (thundering-herd protection).
_CACHE: dict[int, _AllowlistCache] = {}
_LOCKS: dict[int, asyncio.Lock] = {}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _LOCKS:
        _LOCKS[user_id] = asyncio.Lock()
    return _LOCKS[user_id]


async def _get_cached_snapshots(
    *,
    user_id: int,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime],
) -> dict[str, SymbolSnapshot]:
    """Read-through cache for latest snapshots per symbol."""
    now = now_fn()
    cache = _CACHE.get(user_id)
    if cache is not None and cache.is_fresh(now):
        return cache.snapshot_map  # type: ignore[return-value]

    async with _get_lock(user_id):
        # Re-check after acquiring lock (another task may have refreshed)
        cache = _CACHE.get(user_id)
        if cache is not None and cache.is_fresh(now):
            return cache.snapshot_map  # type: ignore[return-value]

        from datetime import timedelta
        snaps = await load_latest_snapshots_per_symbol(session)
        ttl_seconds = settings.SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS
        new_cache = _AllowlistCache(
            snapshot_map=snaps,  # type: ignore[arg-type]
            last_refresh=now,
            ttl=timedelta(seconds=ttl_seconds),
        )
        _CACHE[user_id] = new_cache
        return snaps


async def _apply_symbol_allowlist_gate(
    *,
    proposal,
    user_id: int,
    session: AsyncSession,
    settings,
    now_fn: Callable[[], datetime] = _utc_now,
):
    """Return DispatchResult to block; None to let the trade proceed.

    Decision order:
      1. Flag disabled → None (no-op; entire filter is opt-in).
      2. Stablecoin pair (base in SHADOW_STABLECOIN_EXCLUDE_LIST) →
         blocked_stablecoin.
      3. Snapshot missing for this symbol → None (defensive — no data
         means no decision; allow until data arrives).
      4. is_symbol_allowed(snapshot) is False → blocked_low_sharpe.
      5. Otherwise → None.

    Fail-open: any exception from the DB read OR rule evaluation returns
    None with a WARNING log. A stuck gate that errored to-blocked would
    shut down all trading on a single DB blip.
    """
    from app.trading.execution.dispatcher import DispatchResult

    if not settings.SYMBOL_ALLOWLIST_ENABLED:
        return None

    if is_stablecoin_pair(proposal.symbol, settings):
        return DispatchResult(
            outcome="blocked_stablecoin",
            detail=f"{proposal.symbol} base in stablecoin exclude list",
        )

    try:
        snaps = await _get_cached_snapshots(
            user_id=user_id, session=session, settings=settings, now_fn=now_fn,
        )
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning(
            "symbol_allowlist_gate snapshot read failed for user=%s symbol=%s; "
            "failing open: %s",
            user_id, proposal.symbol, e,
        )
        return None

    snap = snaps.get(proposal.symbol)
    if snap is None:
        # No snapshot row yet → defensive allow. Daily worker will
        # eventually backfill.
        return None

    try:
        # SymbolSnapshot is a frozen dataclass; _SnapshotProto expects
        # settable attrs structurally. is_symbol_allowed only reads
        # .trades_count and .sharpe — safe at runtime.
        allowed = is_symbol_allowed(
            snap,  # type: ignore[arg-type]
            grace_trades=settings.SYMBOL_ALLOWLIST_GRACE_TRADES,
        )
    except Exception as e:  # noqa: BLE001 — fail-open
        log.warning(
            "symbol_allowlist_gate rule eval failed for %s; failing open: %s",
            proposal.symbol, e,
        )
        return None

    if allowed:
        return None
    return DispatchResult(
        outcome="blocked_low_sharpe",
        detail=f"{proposal.symbol}: sharpe={snap.sharpe} trades={snap.trades_count}",
    )
