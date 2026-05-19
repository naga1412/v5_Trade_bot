"""FU-28 UI freshness monitor.

Every poll-interval (5 min default), check whether the pnl_tick WS
stream is delivering events while there are open positions. If the
stream is stale beyond threshold, log WARNING + heartbeat 'degraded';
optionally recycle shadow_worker when FU28_AUTO_RECYCLE_ENABLED.

Default-safe: recycle is OFF by default (shadow_worker is stateful).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.heartbeat import record_heartbeat
from app.shadow.persistence import list_open_positions
from app.ws.shadow_updates import get_last_pnl_tick_at


log = logging.getLogger(__name__)

_POLL_INTERVAL_DEFAULT = 300.0


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def run_one_freshness_check(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Any,
    now_fn: Callable[[], datetime] = _utc_now,
    recycle_fn: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """One pass: returns the report dict + records heartbeat.

    ``recycle_fn`` is optional + injection-friendly so unit tests can
    assert the auto-recycle branch without monkey-patching. Production
    callers leave it None — the loop never auto-recycles in that case
    even if ``FU28_AUTO_RECYCLE_ENABLED`` is True, because
    ``shadow_worker`` is registered as ``stateful=True`` and is NOT
    listed in worker_supervisor's restart-safe set (see
    ``app/ops/worker_supervisor.py`` safety contract).
    """
    now = now_fn()
    threshold = settings.FU28_STALE_PNL_TICK_THRESHOLD_SECONDS
    auto_recycle = bool(settings.FU28_AUTO_RECYCLE_ENABLED)

    try:
        async with session_factory() as session:
            open_positions = await list_open_positions(session, user_id=1)
        open_count = len(open_positions)
        last_emit = get_last_pnl_tick_at()
        symbols_open = {p.symbol for p in open_positions}
        relevant_emits = [last_emit[s] for s in symbols_open if s in last_emit]
        if not relevant_emits:
            newest_age_s: float | None = None
        else:
            newest_age_s = (now - max(relevant_emits)).total_seconds()

        stale = (
            open_count > 0
            and (newest_age_s is None or newest_age_s > threshold)
        )

        if stale:
            log.warning(
                "ui_freshness_monitor: stale pnl_tick — open=%d, "
                "newest_age=%s, threshold=%ds, auto_recycle=%s",
                open_count, newest_age_s, threshold, auto_recycle,
            )
            await record_heartbeat(
                session_factory, "ui_freshness_monitor",
                status="degraded",
                details={
                    "open": open_count,
                    "newest_age_s": newest_age_s,
                    "threshold_s": threshold,
                    "auto_recycle": auto_recycle,
                },
            )
            if auto_recycle and recycle_fn is not None:
                try:
                    await recycle_fn("shadow_worker")
                except Exception as e:  # noqa: BLE001
                    log.error("ui_freshness_monitor: recycle failed: %s", e)
        else:
            await record_heartbeat(
                session_factory, "ui_freshness_monitor", status="ok",
                details={
                    "open": open_count, "newest_age_s": newest_age_s,
                },
            )

        return {
            "open": open_count, "newest_age_s": newest_age_s,
            "threshold_s": threshold, "stale": stale,
        }
    except Exception as e:  # noqa: BLE001
        log.error("ui_freshness_monitor cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, "ui_freshness_monitor",
                status="error", details={"error": str(e)[:200]},
            )
        except Exception:  # noqa: BLE001
            pass
        return {"error": str(e)}


async def run_ui_freshness_monitor_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], Any],
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop. Fires one freshness check per poll-interval."""
    log.info("ui_freshness_monitor: starting")
    while True:
        try:
            settings = settings_factory()
            poll_s = float(getattr(
                settings, "FU28_POLL_INTERVAL_SECONDS", _POLL_INTERVAL_DEFAULT,
            ))
            await _sleep(poll_s)
        except asyncio.CancelledError:
            raise
        try:
            await run_one_freshness_check(
                session_factory=session_factory,
                settings=settings_factory(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("ui_freshness_monitor outer-loop error: %s", e)


def start_ui_freshness_monitor(
    session_factory: async_sessionmaker[AsyncSession],
    settings_factory: Callable[[], Any],
) -> asyncio.Task[None]:
    return asyncio.create_task(run_ui_freshness_monitor_loop(
        session_factory=session_factory, settings_factory=settings_factory,
    ))


__all__ = [
    "run_one_freshness_check",
    "run_ui_freshness_monitor_loop",
    "start_ui_freshness_monitor",
]
