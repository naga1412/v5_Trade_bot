"""FU-28 UI freshness monitor.

Every poll-interval (5 min default), check whether the pnl_tick WS
stream is delivering events while there are open positions. If the
stream is stale beyond threshold, log WARNING + heartbeat 'degraded'.

2026-08-14 (remediation work order E1): the optional shadow_worker
auto-recycle path (FU28_AUTO_RECYCLE_ENABLED) was removed -- it was
structurally inert (the production loop never passed the recycle
callback), by design: shadow_worker is stateful, and auto-restarting
it mid-flight needs real design work this module never had. Detection
+ heartbeat degradation stays; the dead recycle branch does not.

PR-CLEANUP-BATCH-1 §H — per-symbol completeness:
    The pre-fix `relevant_emits` computation silently filtered
    never-emitting symbols out of the staleness calc. A symbol whose
    SPOT subscription never fires (universe drift, blacklist, or
    first-tick-lag on a fresh open position) left the healer reporting
    "ok" with `newest_age=None`. This module now distinguishes:
      - ``missing_symbols``: open position held >5 min, no emit ever
        recorded for that symbol, AND symbol NOT in
        ``SHADOW_SPOT_BLACKLIST`` (blacklisted is upstream-explained).
      - ``stale_symbols``: emit recorded but older than threshold.
    Status goes to ``degraded`` if either list is non-empty.

    Resubscribe metric: a process-local sliding-window counter tracks
    consecutive missing events per symbol over 10 min. ≥3 events in
    that window logs a `healer_resubscribe_requested_total` metric
    line (observability scaffolding — does NOT actually resubscribe;
    operator action still required via existing watchdog probes).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.heartbeat import record_heartbeat
from app.shadow.persistence import list_open_positions
from app.ws.shadow_updates import get_last_pnl_tick_at


log = logging.getLogger(__name__)

_POLL_INTERVAL_DEFAULT = 300.0

# PR-CLEANUP-BATCH-1 §H constants.
# Held-position grace: positions opened <5 min ago aren't flagged for
# missing emits — the first pnl_tick can lag the open by one bar.
_HELD_POSITION_GRACE_SECONDS = 300.0
# Resubscribe sliding window — 3 missing events in 10 min → log metric.
_RESUBSCRIBE_WINDOW_SECONDS = 600.0
_RESUBSCRIBE_THRESHOLD = 3

# Process-local sliding-window counter for missing-event tracking.
# Keyed by symbol → list of UTC datetimes (events older than window
# pruned at every check). Reset on process restart, which is fine for
# observability scaffolding.
_missing_event_counts: dict[str, list[datetime]] = {}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _record_missing_event(symbol: str, now: datetime) -> int:
    """Record a missing-event tick for ``symbol``; return the count
    within the trailing ``_RESUBSCRIBE_WINDOW_SECONDS`` (this tick
    included). Caller decides whether to emit the resubscribe metric.
    """
    window_start = now - timedelta(seconds=_RESUBSCRIBE_WINDOW_SECONDS)
    events = _missing_event_counts.setdefault(symbol, [])
    # Prune events outside the window.
    events[:] = [e for e in events if e >= window_start]
    events.append(now)
    return len(events)


async def run_one_freshness_check(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Any,
    now_fn: Callable[[], datetime] = _utc_now,
) -> dict:
    """One pass: returns the report dict + records heartbeat."""
    now = now_fn()
    threshold = settings.FU28_STALE_PNL_TICK_THRESHOLD_SECONDS
    blacklist = set(getattr(settings, "SHADOW_SPOT_BLACKLIST", []) or [])

    try:
        async with session_factory() as session:
            open_positions = await list_open_positions(session, user_id=1)
        open_count = len(open_positions)
        last_emit = get_last_pnl_tick_at()
        symbols_open = {p.symbol for p in open_positions}

        # PR-CLEANUP-BATCH-1 §H — distinguish missing vs stale.
        # Map symbol → opened_at so the grace-period check is per-symbol.
        opened_at_by_symbol = {p.symbol: getattr(p, "opened_at", None) for p in open_positions}

        def _past_grace(sym: str) -> bool:
            """Position has been open longer than the grace period."""
            opened = opened_at_by_symbol.get(sym)
            if opened is None:
                # Defensive: unknown opened_at → assume past grace.
                return True
            return (now - opened).total_seconds() > _HELD_POSITION_GRACE_SECONDS

        missing_symbols = sorted(
            s for s in symbols_open
            if s not in last_emit
            and s not in blacklist
            and _past_grace(s)
        )
        stale_symbols = sorted(
            s for s in symbols_open
            if s in last_emit
            and (now - last_emit[s]).total_seconds() > threshold
        )

        # Legacy newest_age_s — keeps prior heartbeat field stable.
        # Same computation as pre-fix: max(emit) among open symbols.
        relevant_emits = [last_emit[s] for s in symbols_open if s in last_emit]
        if not relevant_emits:
            newest_age_s: float | None = None
        else:
            newest_age_s = (now - max(relevant_emits)).total_seconds()

        # PR-CLEANUP-BATCH-1 §H: sliding-window counter + resubscribe metric.
        for sym in missing_symbols:
            count = _record_missing_event(sym, now)
            if count >= _RESUBSCRIBE_THRESHOLD:
                # Observability scaffolding — log a structured marker line.
                # Future PR can wire this to an actual resubscribe hook.
                log.warning(
                    "healer_resubscribe_requested_total symbol=%s count=%d "
                    "window_s=%d",
                    sym, count, int(_RESUBSCRIBE_WINDOW_SECONDS),
                )

        # `stale` retained for backward-compat with existing callers; now
        # also true when any per-symbol gap is detected.
        legacy_stale = (
            open_count > 0
            and (newest_age_s is None or newest_age_s > threshold)
        )
        degraded = legacy_stale or bool(missing_symbols) or bool(stale_symbols)

        if degraded:
            log.warning(
                "ui_freshness_monitor: degraded — open=%d, "
                "newest_age=%s, threshold=%ds, missing=%s, stale=%s",
                open_count, newest_age_s, threshold,
                missing_symbols, stale_symbols,
            )
            await record_heartbeat(
                session_factory, "ui_freshness_monitor",
                status="degraded",
                details={
                    "open": open_count,
                    "newest_age_s": newest_age_s,
                    "threshold_s": threshold,
                    "missing_symbols": missing_symbols,
                    "stale_symbols": stale_symbols,
                },
            )
        else:
            await record_heartbeat(
                session_factory, "ui_freshness_monitor", status="ok",
                details={
                    "open": open_count, "newest_age_s": newest_age_s,
                    "missing_symbols": missing_symbols,
                    "stale_symbols": stale_symbols,
                },
            )

        return {
            "open": open_count, "newest_age_s": newest_age_s,
            "threshold_s": threshold, "stale": legacy_stale,
            "missing_symbols": missing_symbols,
            "stale_symbols": stale_symbols,
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
