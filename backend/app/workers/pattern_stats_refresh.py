"""Daily pattern_stats refresh worker.

Re-recomputes pattern_stats from all closed shadow_trades (see
app.ml.patterns.update_pattern_stats). Heartbeats per cycle so
worker_watchdog can alarm if this silently stops running — same pattern
as p_win_refit.

This worker is the fix for why pattern_stats went unpopulated for
months without anyone noticing: update_pattern_stats existed and was
correct-shaped-enough to have a passing unit test, but nothing ever
called it in the running application, and nothing was watching for that
silence. Registered here specifically so the healer can see it stop.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ml.patterns import update_pattern_stats
from app.ops.heartbeat import record_heartbeat


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 86400.0  # 24h


async def run_one_pattern_stats_refresh_cycle(
    *, session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """One cycle: recompute pattern_stats, heartbeat on success/error.

    Returns True on success (heartbeat 'ok'), False on failure (heartbeat
    'error').
    """
    try:
        async with session_factory() as session:
            n_upserted = await update_pattern_stats(session)
            await session.commit()
        await record_heartbeat(
            session_factory, "pattern_stats_refresh",
            status="ok",
            details={"n_upserted": n_upserted},
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("pattern_stats_refresh cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, "pattern_stats_refresh",
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001
            pass
        return False


async def run_pattern_stats_refresh_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle immediately on start, then once per
    ``poll_interval_s`` after that.

    Cycle-first-then-sleep, matching symbol_allowlist_refresh's fix
    (PR #344) and p_win_refit's own precedent: sleeping before the first
    cycle would delay the first heartbeat by a full 24h on every
    restart, and restart cascades within 24h of each other would starve
    the worker entirely.
    """
    log.info("pattern_stats_refresh: starting (interval=%.0fs)", poll_interval_s)
    while True:
        try:
            await run_one_pattern_stats_refresh_cycle(session_factory=session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("pattern_stats_refresh outer-loop error: %s", e)
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise


def start_pattern_stats_refresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_pattern_stats_refresh_loop(
        session_factory=session_factory,
    ))


__all__ = [
    "run_one_pattern_stats_refresh_cycle",
    "run_pattern_stats_refresh_loop",
    "start_pattern_stats_refresh",
]
