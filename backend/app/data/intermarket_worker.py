"""Intermarket snapshot + cleanup loops (SP-3.5 Phase C1/C3/C4).

Mirrors the SP-9 ``news.ingest_worker`` pattern:

* :func:`run_intermarket_snapshot_loop` — every 5 min, polls the
  BinanceFuturesIntermarketAdapter for each symbol in the current top-30
  universe, persists the non-None results in one bulk transaction.
* :func:`run_intermarket_cleanup_loop` — wakes once a day at 04:30 UTC
  (offset from SP-9's 04:00 cleanup) and deletes ``intermarket_snapshots``
  rows older than 14 days.

Both loops accept injected ``_sleep``/``_universe_loader``/``_adapter`` so
unit tests run deterministically.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters import get_intermarket_adapter
from app.data.adapters.binance_futures_intermarket import (
    BinanceFuturesIntermarketAdapter,
    IntermarketSnapshot,
)
from app.data.intermarket_persistence import (
    cleanup_old_intermarket,
    persist_intermarket_snapshots,
)
from app.shadow.universe import load_current_universe


log = logging.getLogger(__name__)

INTERMARKET_INTERVAL_S: int = 5 * 60      # poll every 5 min
INTERMARKET_RETENTION_DAYS: int = 14
CLEANUP_HOUR_UTC: int = 4
CLEANUP_MINUTE_UTC: int = 30
UNIVERSE_LIMIT: int = 30


async def _default_universe_loader(session: AsyncSession) -> list[str]:
    """Top-N symbols from the current asset_universe snapshot."""
    rows = await load_current_universe(session)
    return [r.symbol for r in rows[:UNIVERSE_LIMIT]]


async def _snapshot_once(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: BinanceFuturesIntermarketAdapter,
    universe_loader: Callable[[AsyncSession], Awaitable[list[str]]],
) -> int:
    """One tick: load universe, fetch each symbol, persist non-None."""
    async with session_factory() as session:
        symbols = await universe_loader(session)
    snapshots: list[IntermarketSnapshot] = []
    for sym in symbols:
        snap = await adapter.fetch_snapshot(sym)
        if snap is not None:
            snapshots.append(snap)
    if not snapshots:
        return 0
    async with session_factory() as session:
        n = await persist_intermarket_snapshots(session, snapshots)
    log.info("intermarket snapshot: persisted=%d/%d", n, len(symbols))
    return n


async def run_intermarket_snapshot_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    _adapter: BinanceFuturesIntermarketAdapter | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _universe_loader: Callable[[AsyncSession], Awaitable[list[str]]] | None = None,
) -> None:
    adapter = _adapter or get_intermarket_adapter()
    loader = _universe_loader or _default_universe_loader
    while True:
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("intermarket_snapshot: paused, skipping tick")
            await _sleep(float(INTERMARKET_INTERVAL_S))
            continue
        try:
            await _snapshot_once(session_factory, adapter, loader)
        except asyncio.CancelledError:
            log.info("intermarket snapshot loop cancelled")
            raise
        except Exception:  # noqa: BLE001
            log.exception("intermarket snapshot iteration failed")
        await _sleep(float(INTERMARKET_INTERVAL_S))


def _seconds_until_0430_utc(*, now: datetime | None = None) -> int:
    """Seconds until the next 04:30 UTC. If exactly at 04:30, returns 24h."""
    n = now if now is not None else datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    else:
        n = n.astimezone(UTC)
    target = n.replace(
        hour=CLEANUP_HOUR_UTC, minute=CLEANUP_MINUTE_UTC,
        second=0, microsecond=0,
    )
    if target <= n:
        target = target + timedelta(days=1)
    return int((target - n).total_seconds())


async def run_intermarket_cleanup_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    older_than_days: int = INTERMARKET_RETENTION_DAYS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Nightly 04:30 UTC cleanup of intermarket_snapshots > 14d old."""
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)
    while True:
        wait_s = _seconds_until_0430_utc(now=now_fn())
        await _sleep(float(wait_s))
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("intermarket_cleanup: paused, skipping nightly run")
            continue
        try:
            async with session_factory() as session:
                deleted = await cleanup_old_intermarket(
                    session, older_than_days=older_than_days,
                )
            log.info("intermarket cleanup: deleted=%d", deleted)
        except asyncio.CancelledError:
            log.info("intermarket cleanup loop cancelled")
            raise
        except Exception:  # noqa: BLE001
            log.exception("intermarket cleanup loop iteration failed")


def start_intermarket_snapshot_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn :func:`run_intermarket_snapshot_loop` as a background task."""
    return asyncio.create_task(run_intermarket_snapshot_loop(session_factory))


def start_intermarket_cleanup_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn :func:`run_intermarket_cleanup_loop` as a background task."""
    return asyncio.create_task(run_intermarket_cleanup_loop(session_factory))


__all__ = [
    "CLEANUP_HOUR_UTC",
    "CLEANUP_MINUTE_UTC",
    "INTERMARKET_INTERVAL_S",
    "INTERMARKET_RETENTION_DAYS",
    "UNIVERSE_LIMIT",
    "_seconds_until_0430_utc",
    "_snapshot_once",
    "run_intermarket_cleanup_loop",
    "run_intermarket_snapshot_loop",
    "start_intermarket_cleanup_task",
    "start_intermarket_snapshot_task",
]
