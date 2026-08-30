"""Item 0 (2026-08-30): daily refresh of `app.shadow.cohort_cache`'s
futures_only set -- kept OFF the position-open hot path per operator
ruling.

Deliberately decoupled from `live_fleet_universe_refresh_task`'s 6h
cadence: futures_only is a near-static Binance LISTING property
(changes only when Binance lists/delists a pair), not fleet membership
(which changes every batch with market conditions -- the reason
snapshot-joining fleet membership at open time was rejected). Coupling
this to the 6h fleet task would mean an unrelated fleet-refresh outage
also stalls the listing cache, and vice versa. 24h is ample for a
listing that changes on the order of weeks.

Loop shape mirrors `app.shadow.universe_refresh_scheduler` /
`app.workers.symbol_allowlist_refresh` exactly: fires one cycle
immediately on start, heartbeats ok/error per cycle, sleeps AFTER each
cycle (not before the first one) -- see those modules' docstrings for
why sleep-first delays every restart's first heartbeat by a full
interval.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.heartbeat import record_heartbeat
from app.shadow.cohort_cache import refresh_futures_only_cache

log = logging.getLogger(__name__)

WORKER_NAME: str = "futures_only_refresh_task"
POLL_INTERVAL_SECONDS: float = 86400.0  # 24h -- listing data, not fleet data


async def run_one_futures_only_refresh_cycle(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> int:
    """One cycle: refresh the cache -> heartbeat ok/error. Returns the
    new cache size (0 on failure -- refresh_futures_only_cache already
    logs the failure at ERROR and leaves the prior cache in place, this
    function only adds the heartbeat + never lets the exception escape
    so the outer loop survives a transient Binance/network outage)."""
    result = await refresh_futures_only_cache(http)
    if result is not None:
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="ok", details={"futures_only_count": len(result)},
        )
        return len(result)
    try:
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="error", details={"error": "refresh_futures_only_cache failed -- see logs"},
        )
    except Exception:  # noqa: BLE001 -- heartbeat is best-effort
        pass
    return 0


async def run_futures_only_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    poll_interval_s: float = POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle immediately on start, then once per
    ``poll_interval_s`` after that."""
    log.info(
        "futures_only_refresh: starting (interval=%.0fs)", poll_interval_s,
    )
    while True:
        try:
            await run_one_futures_only_refresh_cycle(
                session_factory=session_factory, http=http,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("futures_only_refresh outer-loop error: %s", e)
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise


def start_futures_only_refresh_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the refresh loop as a background task -- wired into main.py's
    lifespan.

    Reuses the shared intermarket adapter's http client rather than
    opening a second independent pool, same convention as
    `app.shadow.universe_refresh_scheduler.start_live_fleet_universe_
    refresh_task`. Local import so importing this module doesn't
    eagerly build a shared httpx client at import time.
    """
    from app.data.adapters import get_intermarket_adapter

    adapter = get_intermarket_adapter()
    assert adapter.http is not None
    return asyncio.create_task(
        run_futures_only_refresh_loop(
            session_factory=session_factory,
            http=adapter.http,
        ),
        name=WORKER_NAME,
    )


__all__ = [
    "POLL_INTERVAL_SECONDS",
    "WORKER_NAME",
    "run_futures_only_refresh_loop",
    "run_one_futures_only_refresh_cycle",
    "start_futures_only_refresh_task",
]
