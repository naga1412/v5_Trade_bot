"""Phase 4 Task 5c -- schedules `refresh_live_fleet_universe` (Task 5).

`refresh_live_fleet_universe` (app.shadow.live_fleet_universe) populates
`live_fleet_universe` -- the single table `ws_keepalive_task` (Task 5b)
and `futures_poll_task` (Task 8/17) both read to decide which symbols to
poll for predictions. It had never had a scheduled caller anywhere in
the application: confirmed on staging 2026-08-18, `ws_keepalive_task`
heartbeats healthy with `{"children": 0}` because `live_fleet_universe`
is permanently empty. See the incident/decision record at
docs/superpowers/decisions/2026-08-19-live-fleet-universe-never-
scheduled-incident.md for the full severity writeup and the four
operator rulings this module implements (in-process asyncio worker
registered so the healer/watchdog can see it stop; 6h cadence; fire
once on start; cold-start entry-bar relaxation -- see
app.shadow.live_fleet_universe's own module docstring for that piece).

Loop shape mirrors app.workers.symbol_allowlist_refresh's
run_symbol_allowlist_refresh_loop exactly: fires one cycle immediately
on start, heartbeats ok/error per cycle, sleeps AFTER each cycle (not
before the first one). That module's own docstring explains why the
sleep-first shape is wrong: "every backend restart delayed the first
heartbeat... restart cascades starved the worker entirely."

NAMING NOTE (deviation from the plan doc's illustrative snippet): the
plan doc's Task 5c snippets name this worker "universe_refresh_task"
and its entrypoint "start_universe_refresh_task"/"run_universe_refresh_
loop". Both names are ALREADY TAKEN by a distinct, pre-existing, actively
-wired worker -- app.shadow.universe_refresh's daily 00:00 UTC
asset_universe (top-30-by-volume) refresh, imported in main.py as
`from app.shadow.universe_refresh import start_universe_refresh_task`
and registered in worker_registry.py as WorkerSpec(name="universe_
refresh_task", ...). Reusing that name here would make two unrelated
workers share one `worker_heartbeats` row (worker_name is the primary
key) and one WORKER_REGISTRY entry name -- the watchdog would attribute
one worker's heartbeats to the other and `by_name()` would only ever
find the first-registered spec. This module uses the distinct name
`live_fleet_universe_refresh_task` throughout (WORKER_NAME, the
asyncio.Task name, the WorkerSpec entry, and every function name below)
to avoid that collision. See the PR description for the full note.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.ratelimit import RateLimitedClient
from app.ops.heartbeat import record_heartbeat
from app.shadow.live_fleet_universe import refresh_live_fleet_universe

log = logging.getLogger(__name__)

WORKER_NAME: str = "live_fleet_universe_refresh_task"
POLL_INTERVAL_SECONDS: float = 6 * 60 * 60  # 6h, ratified 2026-08-19 (was daily in the original addendum)


async def run_one_universe_refresh_cycle(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    rate_client: RateLimitedClient,
) -> int:
    """One cycle: refresh_live_fleet_universe -> heartbeat ok/error.

    Returns the count of entries persisted this cycle (0 on error).
    Never raises past this function -- any refresh_live_fleet_universe
    exception is caught, logged, and heartbeated as status='error' so
    the outer loop survives a transient Binance/DB outage instead of
    dying silently.
    """
    try:
        entries = await refresh_live_fleet_universe(session_factory, http, rate_client)
        cohort_counts: dict[str, int] = {}
        for entry in entries:
            cohort_counts[entry.cohort] = cohort_counts.get(entry.cohort, 0) + 1
        await record_heartbeat(
            session_factory, WORKER_NAME,
            status="ok",
            details={"entries": len(entries), "cohort_counts": cohort_counts},
        )
        log.info(
            "live_fleet_universe_refresh: cycle complete -- %d entries %s",
            len(entries), cohort_counts,
        )
        return len(entries)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.error("live_fleet_universe_refresh cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001 -- heartbeat is best-effort
            pass
        return 0


async def run_live_fleet_universe_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    rate_client: RateLimitedClient,
    poll_interval_s: float = POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle immediately on start, then once per
    ``poll_interval_s`` after that.

    Shape mirrors run_symbol_allowlist_refresh_loop exactly (see that
    module's docstring): sleeping BEFORE the first cycle would delay
    every backend restart's first heartbeat by a full poll_interval_s,
    and restart cascades within one interval of each other would starve
    the worker entirely. Firing immediately means a fresh environment
    (or a restarted one) never sits dark waiting for the first tick.
    """
    log.info(
        "live_fleet_universe_refresh: starting (interval=%.0fs)", poll_interval_s,
    )
    while True:
        try:
            await run_one_universe_refresh_cycle(
                session_factory=session_factory, http=http, rate_client=rate_client,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("live_fleet_universe_refresh outer-loop error: %s", e)
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise


def start_live_fleet_universe_refresh_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the refresh loop as a background task -- wired into main.py's
    lifespan.

    Constructs http/rate_client from the shared intermarket adapter
    singleton (app.data.adapters.get_intermarket_adapter) rather than
    opening a second, independent HTTP client pool -- matches the
    pattern app.ws.futures_poll's _default_futures_runner and
    app.core.features.flow_features's _resolve_rate_client already use
    for the same reason. Confirmed against the real
    BinanceFuturesIntermarketAdapter dataclass (app.data.adapters.
    binance_futures_intermarket) that `.http` and `.rate_client` are the
    actual attribute names -- both are guaranteed non-None after
    __post_init__.

    Local import so importing this module doesn't eagerly build a
    shared httpx client / DB session factory at import time (same
    convention as the two call sites cited above).
    """
    from app.data.adapters import get_intermarket_adapter

    adapter = get_intermarket_adapter()
    assert adapter.http is not None
    assert adapter.rate_client is not None
    return asyncio.create_task(
        run_live_fleet_universe_refresh_loop(
            session_factory=session_factory,
            http=adapter.http,
            rate_client=adapter.rate_client,
        ),
        name=WORKER_NAME,
    )


__all__ = [
    "POLL_INTERVAL_SECONDS",
    "WORKER_NAME",
    "run_live_fleet_universe_refresh_loop",
    "run_one_universe_refresh_cycle",
    "start_live_fleet_universe_refresh_task",
]
