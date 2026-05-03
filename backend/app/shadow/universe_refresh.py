"""Daily universe refresh — wakes once a day at 00:00 UTC.

The loop is structured as:
    1. compute seconds until the next refresh hour (default: 00:00 UTC)
    2. sleep that many seconds
    3. fetch the top-N USDT-quoted Binance Futures perpetuals
    4. persist a snapshot row
    5. set the asyncio.Event so the worker can rebuild its symbol streams

Sleep, fetch, and now() are injectable so the loop is fully unit-testable
without real time, real Binance traffic, or real DB.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shadow.universe import (
    AssetUniverseEntry,
    fetch_top_n_usdt_futures,
    save_universe_snapshot,
)

log = logging.getLogger(__name__)

UNIVERSE_TOP_N: int = 30
DEFAULT_REFRESH_HOUR_UTC: int = 0


class _FetchFn(Protocol):
    async def __call__(
        self, http: httpx.AsyncClient, *, n: int = UNIVERSE_TOP_N,
    ) -> list[AssetUniverseEntry]: ...


def seconds_until_next_utc(hour: int, now: datetime) -> int:
    """Return the integer number of seconds from ``now`` until the next UTC ``hour``.

    Naive datetimes are interpreted as UTC. If ``now`` already sits exactly at
    the target hour, the next occurrence is 24h away — never zero, so the
    caller never busy-loops.
    """
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    delta = target - now
    return int(delta.total_seconds())


async def _default_fetch(
    http: httpx.AsyncClient, *, n: int = UNIVERSE_TOP_N,
) -> list[AssetUniverseEntry]:
    return await fetch_top_n_usdt_futures(http, n=n)


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    entries: list[AssetUniverseEntry],
) -> None:
    async with session_factory() as session:
        await save_universe_snapshot(session, entries)
        await session.commit()


async def run_universe_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    refreshed_event: asyncio.Event,
    wake_at_utc_hour: int = DEFAULT_REFRESH_HOUR_UTC,
    top_n: int = UNIVERSE_TOP_N,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _fetch: _FetchFn | None = None,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Run the refresh loop forever — until cancelled.

    Each iteration:
        - sleep until the next ``wake_at_utc_hour``
        - fetch + persist the universe snapshot
        - signal ``refreshed_event`` so listeners can react

    Errors during fetch/persist are swallowed (logged) so the loop survives
    transient Binance or DB outages.
    """
    fetch = _fetch if _fetch is not None else _default_fetch
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)

    while True:
        wait_s = seconds_until_next_utc(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))

        try:
            async with httpx.AsyncClient() as http:
                entries = await fetch(http, n=top_n)
            if entries:
                await _persist(session_factory, entries)
                refreshed_event.set()
                log.info("universe refreshed: %d entries", len(entries))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("universe refresh failed: %s", e)


def start_universe_refresh_task(
    session_factory: async_sessionmaker[AsyncSession],
    refreshed_event: asyncio.Event,
    *,
    wake_at_utc_hour: int = DEFAULT_REFRESH_HOUR_UTC,
) -> asyncio.Task[None]:
    """Spawn the refresh loop as a background task. Wired into the lifespan."""
    return asyncio.create_task(run_universe_refresh_loop(
        session_factory=session_factory,
        refreshed_event=refreshed_event,
        wake_at_utc_hour=wake_at_utc_hour,
    ))


__all__ = [
    "DEFAULT_REFRESH_HOUR_UTC",
    "UNIVERSE_TOP_N",
    "run_universe_refresh_loop",
    "seconds_until_next_utc",
    "start_universe_refresh_task",
]
