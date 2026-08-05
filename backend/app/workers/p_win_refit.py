"""PR5 daily p_win_refit worker.

Re-fits the per-direction isotonic p_win calibration models nightly
against the latest closed shadow_trades (see
app.core.scoring.p_win_calibrator.fit_p_win_models). Heartbeats per
cycle so worker_watchdog can alarm if this silently stops running —
same pattern as symbol_allowlist_refresh (PR10).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scoring.p_win_calibrator import (
    P_WIN_MODEL_PATH_LONG,
    P_WIN_MODEL_PATH_SHORT,
    fit_p_win_models,
)
from app.ops.heartbeat import record_heartbeat


log = logging.getLogger(__name__)


_POLL_INTERVAL_SECONDS = 86400.0  # 24h


async def run_one_refit_cycle(
    *, session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """One cycle: fit both direction models, heartbeat on success/error.

    Returns True on success (heartbeat 'ok'), False on failure (heartbeat
    'error'). fit_p_win_models itself is fail-open (logs + returns on
    sklearn-missing or insufficient-data) so this rarely hits the except
    branch — it's there for genuinely unexpected failures (DB errors,
    disk-write failures on the .pkl).
    """
    try:
        async with session_factory() as session:
            await fit_p_win_models(session)
        await record_heartbeat(
            session_factory, "p_win_refit",
            status="ok",
            details={
                "long_model_exists": P_WIN_MODEL_PATH_LONG.exists(),
                "short_model_exists": P_WIN_MODEL_PATH_SHORT.exists(),
            },
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("p_win_refit cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, "p_win_refit",
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001
            pass
        return False


async def run_p_win_refit_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_s: float = _POLL_INTERVAL_SECONDS,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one cycle immediately on start, then once per
    ``poll_interval_s`` after that.

    Cycle-first-then-sleep, matching symbol_allowlist_refresh's fix
    (PR #344): sleeping before the first cycle would delay the first
    heartbeat by a full 24h on every restart, and restart cascades
    within 24h of each other would starve the worker entirely.
    """
    log.info("p_win_refit: starting (interval=%.0fs)", poll_interval_s)
    while True:
        try:
            await run_one_refit_cycle(session_factory=session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("p_win_refit outer-loop error: %s", e)
        try:
            await _sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise


def start_p_win_refit(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_p_win_refit_loop(
        session_factory=session_factory,
    ))


__all__ = [
    "run_one_refit_cycle",
    "run_p_win_refit_loop",
    "start_p_win_refit",
]
