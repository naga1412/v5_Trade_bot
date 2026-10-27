"""The retrieval path for guards, probes and counters -- detection only.

Three rules, which are the whole design:

1. EVERYTHING GETS A ROW. A counter in process memory is lost on restart;
   a log line is lost when docker truncates the buffer on redeploy. Both
   have happened here repeatedly (see the 0044 migration docstring for
   the five instances).

2. SILENCE IS NOT SUCCESS. A probe emits a row even when it passes, so
   that the ABSENCE of a row means "did not run" rather than "nothing to
   say". :func:`find_silent_checks` therefore iterates
   :data:`EXPECTED_CHECKS` -- a declared registry -- and looks each name
   up. It deliberately does NOT ``GROUP BY check_name`` over the table,
   because a grouped query can only return checks that already reported.
   The check that never ran contributes no row, no group, and no alarm --
   which is exactly how `phase4-universe-discrepancy-check` stayed
   invisible for months while every run exited 0.

3. GRANULARITY MATCHES THE ALARM, NOT THE EVENT. Sparse alarms get a row
   each (:func:`record_event`). Continuous ones get a per-day counter
   (:func:`record_daily`). #610's mtf fetch failure is 56 symbols times
   the scoring cadence; per-event rows there would be write amplification
   wearing an instrumentation costume.

NOTHING IN THIS MODULE TAKES AN ACTION. There is no remediation hook, no
retry, no auto-disable, and that is a decision rather than an omission:
`register_brain.py:205` healed automatically ("no active champion =
resolve it"), installed untested brains for 64 nights, and was then
tripped by its own verification probe. #571's fix was refusing to heal by
default. This module reports; a human decides.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

CheckKind = Literal["guard", "probe", "counter"]
# "ok" is NOT decoration. It is the row that distinguishes "ran and found
# nothing" from "did not run", which is rule 2.
CheckStatus = Literal["ok", "violation", "error"]
Granularity = Literal["event", "daily"]

__all__ = [
    "EXPECTED_CHECKS",
    "CheckKind",
    "CheckStatus",
    "ExpectedCheck",
    "Granularity",
    "SilentCheck",
    "QUEUE_PROGRESS_PREFIX",
    "STALL_ALARM_PASSES",
    "find_silent_checks",
    "flush_mtf_fetch_failures",
    "record_daily",
    "record_event",
    "record_queue_pass",
]


@dataclass(frozen=True)
class ExpectedCheck:
    """A check whose SILENCE is itself a finding.

    ``max_silence`` is how long this check may go without emitting any row
    before it is reported. It is the check's own cadence plus slack, never
    a global constant -- a 24h probe and a 5-minute flusher are not
    interchangeable, and the watchdog's own history here is of crying wolf
    when one staleness number was applied to every worker.
    """

    name: str
    kind: CheckKind
    max_silence: _dt.timedelta
    why: str


@dataclass(frozen=True)
class SilentCheck:
    """A registry entry that has not reported inside its own window."""

    name: str
    kind: CheckKind
    max_silence: _dt.timedelta
    # None means NEVER REPORTED, which is strictly worse than stale: there
    # is no evidence the check has ever executed. Kept as a separate state
    # rather than folded into a very large age, so a caller cannot format
    # the two identically (cf. the not_evaluated collapse, #610).
    last_seen_at: _dt.datetime | None
    age: _dt.timedelta | None
    why: str

    @property
    def never_reported(self) -> bool:
        return self.last_seen_at is None


# ---------------------------------------------------------------------------
# The registry.
#
# Deliberately SMALL. Registering a check that was never wired would make
# this detector report it silent forever on its first run, and twenty
# standing false alarms is how an alarm channel gets ignored. Entries are
# added as each producer is actually wired, and the migration path for the
# remaining known cases is named in the PR rather than pre-registered here.
# ---------------------------------------------------------------------------
EXPECTED_CHECKS: dict[str, ExpectedCheck] = {
    "mtf_kline_fetch_failed": ExpectedCheck(
        name="mtf_kline_fetch_failed",
        kind="counter",
        # The flusher runs every 5 minutes and emits an "ok" row even with
        # zero failures, so 20 minutes is four missed cycles -- long
        # enough that a single slow cycle is not an alarm, short enough
        # that a dead flusher surfaces the same hour.
        max_silence=_dt.timedelta(minutes=20),
        why=(
            "#610's per-symbol MTF kline-fetch counter lives in a "
            "module-level dict that dies with the process. Silence here "
            "means the flusher stopped, NOT that fetches started working."
        ),
    ),
}


def _is_pg(session: "AsyncSession") -> bool:
    bind = session.get_bind()
    return bool(bind.dialect.name.startswith("postgres"))


def _detail_sql(session: "AsyncSession") -> str:
    """The ``detail`` placeholder, cast for the dialect.

    The column is JSONB on Postgres and TEXT on the SQLite test mirror.
    asyncpg will not implicitly coerce a str parameter into JSONB, so the
    cast is explicit there and absent here.
    """
    return "CAST(:detail AS JSONB)" if _is_pg(session) else ":detail"


def _bucket_value(session: "AsyncSession", day: _dt.date) -> Any:
    """``bucket_date`` bound for the dialect (DATE on PG, TEXT on SQLite)."""
    return day if _is_pg(session) else day.isoformat()


def _encode_detail(detail: dict[str, Any] | None) -> str | None:
    if detail is None:
        return None
    # sort_keys so two writes of the same facts compare equal in a diff.
    return json.dumps(detail, sort_keys=True, default=str)


async def record_event(
    session: "AsyncSession",
    check_name: str,
    *,
    kind: CheckKind,
    status: CheckStatus,
    subject: str = "",
    detail: dict[str, Any] | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Append ONE row for ONE occurrence -- for SPARSE alarms.

    ``bucket_date`` stays NULL, which is what keeps these rows out of the
    daily unique index (NULLs are distinct in a unique index on both
    dialects), so any number of them can coexist on the same day.

    Use :func:`record_daily` instead when the event can recur on every
    cycle. The test for this module asserts that a caller which writes a
    continuous failure through here produces one row per call, so the
    choice is visible rather than accidental.
    """
    stamp = now or _dt.datetime.now(_dt.timezone.utc)
    await session.execute(
        sa.text(
            "INSERT INTO check_events ("
            "  check_name, check_kind, status, granularity, bucket_date,"
            "  subject, observed_count, detail, first_seen_at, last_seen_at"
            ") VALUES ("
            "  :name, :kind, :status, 'event', NULL,"
            f"  :subject, 1, {_detail_sql(session)}, :now, :now"
            ")"
        ),
        {
            "name": check_name, "kind": kind, "status": status,
            "subject": subject, "detail": _encode_detail(detail),
            "now": stamp,
        },
    )


async def record_daily(
    session: "AsyncSession",
    check_name: str,
    *,
    kind: CheckKind,
    status: CheckStatus,
    subject: str = "",
    increment: int = 1,
    detail: dict[str, Any] | None = None,
    now: _dt.datetime | None = None,
) -> None:
    """Add ``increment`` to the (check, subject, day) counter -- CONTINUOUS.

    One row per day per subject however many times it fires.
    ``first_seen_at`` is preserved across increments (it is the first
    sighting that day, which is the number you want when asking "when did
    this start"), while ``last_seen_at`` advances.

    ``increment=0`` is MEANINGFUL and supported: it is how a flusher says
    "I ran, and there was nothing to report". Without it, a healthy
    flusher would be indistinguishable from a dead one -- rule 2.
    """
    stamp = now or _dt.datetime.now(_dt.timezone.utc)
    await session.execute(
        sa.text(
            "INSERT INTO check_events ("
            "  check_name, check_kind, status, granularity, bucket_date,"
            "  subject, observed_count, detail, first_seen_at, last_seen_at"
            ") VALUES ("
            "  :name, :kind, :status, 'daily', :bucket,"
            f"  :subject, :inc, {_detail_sql(session)}, :now, :now"
            ") ON CONFLICT (check_name, subject, bucket_date) DO UPDATE SET"
            "  observed_count = check_events.observed_count + :inc,"
            "  last_seen_at = :now,"
            "  status = :status,"
            f"  detail = {_detail_sql(session)}"
        ),
        {
            "name": check_name, "kind": kind, "status": status,
            "bucket": _bucket_value(session, stamp.date()),
            "subject": subject, "inc": increment,
            "detail": _encode_detail(detail), "now": stamp,
        },
    )


async def find_silent_checks(
    session: "AsyncSession",
    *,
    now: _dt.datetime | None = None,
) -> list[SilentCheck]:
    """Registry entries that have not reported inside their own window.

    ITERATES THE REGISTRY, not the table. A ``GROUP BY check_name`` over
    check_events would be the obvious implementation and it is the bug:
    the check that never ran has no rows, forms no group, and would be
    reported as healthy by omission. That is the precise failure mode of
    `phase4-universe-discrepancy-check`, which was a dead script for
    months while every run exited 0.

    Returns findings. Takes no action -- see the module docstring.
    """
    stamp = now or _dt.datetime.now(_dt.timezone.utc)
    out: list[SilentCheck] = []
    for name, expected in EXPECTED_CHECKS.items():
        row = (
            await session.execute(
                sa.text(
                    "SELECT MAX(last_seen_at) AS seen FROM check_events "
                    "WHERE check_name = :name"
                ),
                {"name": name},
            )
        ).first()
        seen = row.seen if row is not None else None
        if isinstance(seen, str):  # SQLite stores TIMESTAMPTZ as TEXT
            seen = _dt.datetime.fromisoformat(seen)
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=_dt.timezone.utc)
        if seen is None:
            out.append(SilentCheck(
                name=name, kind=expected.kind,
                max_silence=expected.max_silence,
                last_seen_at=None, age=None, why=expected.why,
            ))
            continue
        age = stamp - seen
        if age > expected.max_silence:
            out.append(SilentCheck(
                name=name, kind=expected.kind,
                max_silence=expected.max_silence,
                last_seen_at=seen, age=age, why=expected.why,
            ))
    return out


async def flush_mtf_fetch_failures(
    session: "AsyncSession",
    *,
    now: _dt.datetime | None = None,
    counts: dict[str, int] | None = None,
    detail: dict[tuple[str, str, str], int] | None = None,
) -> int:
    """Give #610's in-memory counter a home. Returns symbols written.

    The counter is CUMULATIVE SINCE PROCESS START, so this writes the
    DELTA since the previous flush rather than the absolute value --
    otherwise every flush would re-add the whole running total. The
    previous snapshot lives in module memory beside the counter it
    mirrors, so a process restart resets both together and the deltas
    stay consistent.

    A zero-failure cycle still writes an ``ok`` row with
    ``increment=0``. That row is the only thing distinguishing a healthy
    flusher from a dead one, which is rule 2.
    """
    from app.core.scoring.mtf_confluence import (
        get_fetch_failure_counts,
        get_fetch_failure_detail,
    )

    stamp = now or _dt.datetime.now(_dt.timezone.utc)
    current = dict(counts) if counts is not None else get_fetch_failure_counts()

    written = 0
    for symbol, total in sorted(current.items()):
        delta = total - _last_flushed.get(symbol, 0)
        if delta <= 0:
            # Already recorded, or the counter went backwards (restart).
            # Re-synchronise rather than writing a negative increment.
            _last_flushed[symbol] = total
            continue
        await record_daily(
            session, "mtf_kline_fetch_failed",
            kind="counter", status="violation", subject=symbol,
            increment=delta,
            detail={"cumulative_since_process_start": total},
            now=stamp,
        )
        _last_flushed[symbol] = total
        written += 1

    # THE DETAIL SERIES, under its OWN check_name so the symbol-level
    # series above keeps its continuity -- the 2026-10-26
    # permanent/transient classification was made from that series.
    #
    # subject is "<SYMBOL>/<tf>/<failure>", e.g.
    # "1000PEPEUSDT/1w/HTTPStatusError_400" vs
    # "BNBUSDT/1w/ReadTimeout". One query then separates a routing
    # failure (every timeframe, one status code, persistent) from a
    # timeout (long timeframes, a transport exception) WITHOUT needing
    # two manual reads of consecutive hourly cycles.
    if detail is None:
        detail = get_fetch_failure_detail()
    for (sym, tf, kind), total in sorted(detail.items()):
        dkey = (sym, tf, kind)
        delta = total - _last_flushed_detail.get(dkey, 0)
        if delta <= 0:
            _last_flushed_detail[dkey] = total
            continue
        await record_daily(
            session, "mtf_kline_fetch_failed_detail",
            kind="counter", status="violation",
            subject=f"{sym}/{tf}/{kind}", increment=delta,
            detail={"cumulative_since_process_start": total},
            now=stamp,
        )
        _last_flushed_detail[dkey] = total

    # The heartbeat row, ALWAYS written, subject='' so it never collides
    # with a per-symbol row.
    await record_daily(
        session, "mtf_kline_fetch_failed",
        kind="counter",
        status="violation" if written else "ok",
        subject="",
        increment=0,
        detail={"symbols_written_this_flush": written,
                "symbols_failing_total": len(current)},
        now=stamp,
    )
    return written


QUEUE_PROGRESS_PREFIX = "queue_progress"
# Consecutive stalled passes before the onset is escalated. 3 passes at
# the validator's 60s cadence is ~3 minutes: long enough that a single
# slow or contended pass is not an alarm, short enough that nothing
# approaching 53 days is possible. The DAILY row records every stalled
# pass regardless, so the threshold only governs escalation, never
# whether the data is kept.
STALL_ALARM_PASSES: int = 3


async def record_queue_pass(
    session: "AsyncSession",
    worker: str,
    *,
    selected: int,
    advanced: int,
    now: _dt.datetime | None = None,
) -> int:
    """Record one queue-draining pass. Returns the consecutive-stall streak.

    LIVENESS IS NOT PROGRESS, and that distinction is the whole reason
    this function exists. The 2026-09-03 validator outage was never
    silent at the liveness level: the task ran every 60 seconds for 53
    days and heartbeated ``status="ok"`` every single time. It was
    silent at the PROGRESS level -- alive and stuck. #612's
    :func:`find_silent_checks` cannot see that, by construction: it
    detects a check that FAILED TO EMIT, and this one emitted
    faithfully throughout.

    ``advanced == 0`` ALONE IS NOT AN ALARM. It is the correct state of
    an idle queue, and the old heartbeat already carried it
    (``validated_in_tick: 0``) for every one of those 53 days. The
    diagnostic quantity is the PAIR: ``selected > 0 and advanced == 0``
    means work was available and none of it moved, which no healthy
    state produces.

    GRANULARITY, per the same rule that made the mtf counter daily: a
    pass happens every 60s, so passes are written as DAILY aggregates
    (two subjects -- ``''`` counts all passes, ``'stalled'`` counts the
    stalled ones), while the rare ONSET of a stall gets its own
    ``event`` row so the first moment is durable rather than averaged
    into a day. A per-pass row would be 1,440 writes/worker/day.

    Takes no action. Returns the streak so the caller can log; the
    caller is a worker, and a worker deciding to heal itself is
    `register_brain.py:205`.
    """
    stamp = now or _dt.datetime.now(_dt.timezone.utc)
    name = f"{QUEUE_PROGRESS_PREFIX}:{worker}"
    stalled = selected > 0 and advanced == 0

    # Every pass, so that a day with rows proves the worker ran.
    await record_daily(
        session, name, kind="counter",
        status="violation" if stalled else "ok",
        subject="", increment=1,
        detail={"selected": selected, "advanced": advanced,
                "stalled": stalled},
        now=stamp,
    )
    if stalled:
        await record_daily(
            session, name, kind="counter", status="violation",
            subject="stalled", increment=1,
            detail={"selected": selected, "advanced": advanced},
            now=stamp,
        )
        _stall_streak[worker] = _stall_streak.get(worker, 0) + 1
    else:
        _stall_streak[worker] = 0

    streak = _stall_streak[worker]
    if stalled and streak == STALL_ALARM_PASSES:
        # ONSET ONLY -- written once per stall episode, at the moment the
        # threshold is crossed, not on every pass afterwards. A sparse
        # alarm gets a per-event row; see record_event.
        await record_event(
            session, name, kind="counter", status="violation",
            subject="stall_onset",
            detail={"selected": selected, "advanced": advanced,
                    "consecutive_passes": streak},
            now=stamp,
        )
    return streak


# Consecutive stalled passes per worker. In-memory on purpose: the streak
# is about THIS process's recent behaviour, and a restart genuinely does
# reset it. The durable record is the daily row, where a stall that
# outlives a restart shows up as stalled == total passes on consecutive
# days -- which is what 53 days of this would have looked like.
_stall_streak: dict[str, int] = {}

# Mirrors mtf_confluence's own module-level counter; see
# flush_mtf_fetch_failures for why a snapshot is required.
_last_flushed: dict[str, int] = {}
# Same contract for the (symbol, timeframe, failure) series.
_last_flushed_detail: dict[tuple[str, str, str], int] = {}


def _reset_for_tests() -> None:
    _last_flushed.clear()
    _last_flushed_detail.clear()
    _stall_streak.clear()
