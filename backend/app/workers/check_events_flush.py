"""Flush in-process check counters to `check_events` -- detection only.

WHY A WORKER AND NOT A WRITE AT THE FAILURE SITE. #610's counter is
incremented inside `_fetch_one_tf`, which is on the MTF scoring hot path
and holds no DB session. Writing a row there would mean (a) a
fire-and-forget DB write per failed fetch, which for the futures_poll
cohort is 56 symbols times six timeframes times the scoring cadence --
the write amplification item 3(c) rules out -- and (b) a DB dependency on
a path whose entire contract is to degrade quietly. So the counter stays
in memory where it is cheap, and this worker gives it the retrieval path.

Loop shape mirrors `app.workers.futures_only_refresh` exactly: one cycle
IMMEDIATELY on start, heartbeat ok/error per cycle, sleep AFTER the cycle
rather than before the first one -- sleeping first delays every restart's
first heartbeat by a full interval and has previously made a healthy
worker look dead.

NOTHING HERE REMEDIATES. The cycle reads counters, writes rows, and
heartbeats. It does not clear the counters it reads (the delta bookkeeping
in `check_events.flush_mtf_fetch_failures` makes that unnecessary and a
reset would destroy the cumulative figure a human wants), does not
restart anything, and does not act on what `find_silent_checks` reports.
`register_brain.py:205` is the local precedent for why: it healed by
default and installed untested brains for 64 nights.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.check_events import (
    find_silent_checks,
    flush_mtf_fetch_failures,
    record_universe_tradability,
)
from app.ops.heartbeat import record_heartbeat

log = logging.getLogger(__name__)

WORKER_NAME: str = "check_events_flush_task"
# 5 minutes. The registry's max_silence for mtf_kline_fetch_failed is 20
# minutes, i.e. four missed cycles -- one slow cycle is not an alarm, a
# dead flusher surfaces within the hour.
POLL_INTERVAL_SECONDS: float = 300.0


async def run_one_flush_cycle(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """One cycle: flush counters, report silent checks, heartbeat.

    Returns the number of per-symbol rows written. Never raises -- a
    transient DB error must not kill the loop, and the heartbeat carries
    the error so the failure is visible rather than silent.
    """
    try:
        async with session_factory() as session:
            # ORDER IS LOAD-BEARING TWICE OVER, so both reasons are here.
            #
            # 1. BEFORE flush_mtf_fetch_failures, because that function
            #    advances its in-process watermarks (`_last_flushed`,
            #    `_last_flushed_detail`) as it writes, BEFORE the commit
            #    below. So anything fallible placed AFTER it and before
            #    the commit can roll the rows back while the watermarks
            #    stay advanced -- and those increments are then never
            #    written again, silently under-reporting the counter for
            #    the life of the process. I introduced exactly that
            #    window on 2026-10-31 by adding this call after the
            #    flush; running first removes it, and costs nothing.
            #
            # 2. BEFORE find_silent_checks, because the audit writes its
            #    own row in this same transaction. Reversed, the very
            #    first cycle after deploy would log a spurious
            #    'has NEVER reported' for a check it is about to
            #    produce.
            tradability_finding = await record_universe_tradability(session)
            written = await flush_mtf_fetch_failures(session)
            silent = await find_silent_checks(session)
            await session.commit()
    except Exception as e:  # noqa: BLE001
        log.error("check_events_flush cycle failed: %s", e)
        try:
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:500]},
            )
        except Exception as e2:  # noqa: BLE001
            log.error("check_events_flush heartbeat also failed: %s", e2)
        return 0

    for s in silent:
        # ERROR, not WARNING: a check that is not reporting is a check
        # whose findings nobody is receiving. The two states are logged
        # DIFFERENTLY on purpose -- "never reported" and "stopped
        # reporting" need different human responses, and collapsing them
        # onto one sentence is the #610 defect.
        if s.never_reported:
            log.error(
                "check_silent: %s (%s) has NEVER reported a single row. "
                "There is no evidence it has ever run. %s",
                s.name, s.kind, s.why,
            )
        else:
            log.error(
                "check_silent: %s (%s) last reported %s ago, window is %s. "
                "It ran before and has stopped. %s",
                s.name, s.kind, s.age, s.max_silence, s.why,
            )

    await record_heartbeat(
        session_factory, WORKER_NAME,
        status="ok",
        details={
            "symbols_written": written,
            "tradability_finding": tradability_finding,
            "silent_checks": len(silent),
            "silent_names": sorted(s.name for s in silent)[:20],
        },
    )
    return written


async def run_check_events_flush_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    interval_s: float = POLL_INTERVAL_SECONDS,
) -> None:
    log.info(
        "check_events_flush: starting (interval=%ds)", int(interval_s),
    )
    while True:
        await run_one_flush_cycle(session_factory=session_factory)
        await asyncio.sleep(interval_s)


def start_check_events_flush_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the flush loop as a background task -- wired into main.py.

    No http client is needed: this worker reads process memory and writes
    the DB. That is deliberate -- a flusher that depended on the network
    could be taken out by the same outage it is meant to record.
    """
    return asyncio.create_task(
        run_check_events_flush_loop(session_factory=session_factory),
        name=WORKER_NAME,
    )
