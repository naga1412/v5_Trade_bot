"""Watchdog task — periodically checks every worker's liveness signal.

Runs every 5 minutes. For each entry in WORKER_REGISTRY:
  - Skip if any of ``required_env`` is unset (worker is "expected absent").
  - Run ``liveness_query``; compute ``now() - max(beat_at)``.
  - If staleness > ``max_staleness_seconds``: log.error + alert_admin.
  - For ``stateful=False`` workers that are registered with
    ``app.ops.worker_supervisor``: also call ``supervisor.restart(name)``
    to cancel the dead task and spawn a fresh one. The respawn is logged
    with ``action=restarted`` and re-alerts only if the worker stays
    stale on the next tick.

Stateful workers (live_worker, shadow_worker, liquidation_monitor,
telegram_poller, ws_keepalive_task) get ALERT-ONLY treatment regardless.
Restarting them risks lost open positions, duplicate orders, vault
re-init issues, or Binance per-IP WS rate-limit hits.

The watchdog itself never raises. A query failure for one worker is
logged and the loop continues to the next. A restart failure is logged
and the alert is sent normally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops import worker_supervisor
from app.ops.alerts import alert_admin
from app.ops.heartbeat import record_heartbeat
from app.ops.worker_registry import WORKER_REGISTRY, WorkerSpec

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS: int = 5 * 60

# FU-15: single-shot worker grace period. A single_shot=True task gets this
# long after process start to record its `single_shot_completed` heartbeat.
# Past this grace with no heartbeat → watchdog alarms. 5 min covers worst-case
# container ramp (~30s) + prewarm's 60s deadline + slack.
SINGLE_SHOT_GRACE_SECONDS: float = 5 * 60

# Process start timestamp — used as a proxy for container uptime. Captured at
# module import time, which is when the backend process starts the watchdog
# during app.main lifespan.
_PROCESS_START: float = time.time()


def _container_uptime_seconds() -> float:
    """Seconds since this process started. Approximates container uptime."""
    return time.time() - _PROCESS_START


# States that trigger an alert. Centralized so check_all_workers and
# _alert_if_dead stay in sync.
BAD_STATES: frozenset[str] = frozenset({
    "stale",
    "never_heartbeated",
    # FU-15: single-shot-specific alarming states
    "single_shot_never_completed",
    "single_shot_failed",
    # Healer B1 (2026-07-23): a worker that heartbeats on time with
    # last_status='error' looks 'ok' under the pure-staleness classifier
    # and stays PERMANENTLY BLIND. See the 2026-07-22 -> 2026-07-23
    # symbol_allowlist_refresh incident.
    "heartbeat_error",
})

# Healer B1: how many consecutive error-status beats trigger the alarm.
# Continuous-cadence workers (30s/60s/5min) tolerate one flaky cycle;
# daily-cadence workers must alarm on the first error since a second
# consecutive error means ~24h of blindness. Keyed by worker_name; each
# entry is the minimum consecutive count that promotes the state to
# 'heartbeat_error'.
ERROR_STREAK_ALARM_THRESHOLD_DEFAULT: int = 2
ERROR_STREAK_ALARM_THRESHOLD_DAILY: int = 1
# Daily-cadence workers (max_staleness_seconds > 12h) get the strict N=1
# threshold. Kept as a computed rule rather than a per-worker constant so
# the registry stays the single source of truth for cadence.
_DAILY_CADENCE_S: int = 12 * 60 * 60

# Module-level tracker for consecutive error-status beats. In-memory only
# — resets on backend restart. Adequate for Phase 0 detect-only. Key is
# (worker_name, latest beat_at ISO); we clear the entry once the streak
# hits the alarm threshold OR once a non-error beat arrives.
_ERROR_STREAKS: dict[str, int] = {}
_LAST_SEEN_BEAT_AT: dict[str, datetime] = {}

# Name the watchdog uses to record its OWN heartbeat. Without this the
# watchdog itself is a blind spot — if it crashes silently, the workers
# go unobserved and nothing notices. Host-level watchdog.sh reads this
# row to decide whether the in-process self-healer is still alive.
WATCHDOG_HEARTBEAT_NAME: str = "worker_watchdog_task"


async def _staleness_seconds(
    session_factory: async_sessionmaker[AsyncSession], spec: WorkerSpec,
) -> float | None:
    """Returns seconds since the worker's last heartbeat, or None on error.

    Uses a fresh session per call: asyncpg + Postgres aborts the entire
    transaction on the first SQL error, after which every subsequent
    query on the same session fails with InFailedSQLTransactionError.
    The first prod watchdog pass (PR #97) hit a wrong column name on
    intermarket_snapshots and the cascade poisoned 4 other workers'
    queries. Per-call sessions isolate failures to the offending worker.
    """
    if spec.liveness_query is None:
        return None
    try:
        async with session_factory() as session:
            params = {"n": spec.name} if ":n" in spec.liveness_query else {}
            result = await session.execute(sa.text(spec.liveness_query), params)
            row = result.first()
    except Exception as e:  # noqa: BLE001
        log.warning("watchdog: liveness query failed for %s: %s", spec.name, e)
        return None
    if row is None or row[0] is None:
        # Never heartbeated — return a huge number so it trips the alert.
        # We treat "never" as "infinitely stale".
        return float("inf")
    last = row[0]
    if isinstance(last, str):
        last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds()


def _env_gates_met(spec: WorkerSpec) -> bool:
    import os
    for var in spec.required_env:
        val = os.environ.get(var, "").strip().lower()
        if not val or val in {"false", "0", "no"}:
            return False
    return True


async def _fetch_heartbeat_status(
    session_factory: async_sessionmaker[AsyncSession],
    worker_name: str,
) -> tuple[datetime | None, str | None]:
    """Returns (beat_at, last_status) for a worker, or (None, None) if absent.

    Used by single-shot classification to distinguish 'completed' from
    'failed' status on the latest heartbeat row.
    """
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT beat_at, last_status FROM worker_heartbeats "
                        "WHERE worker_name = :n"
                    ),
                    {"n": worker_name},
                )
            ).first()
    except Exception as e:  # noqa: BLE001
        log.warning("watchdog: heartbeat fetch failed for %s: %s", worker_name, e)
        return (None, None)
    if row is None:
        return (None, None)
    beat_at = row[0]
    last_status = row[1]
    if isinstance(beat_at, str):
        beat_at = datetime.fromisoformat(beat_at.replace("Z", "+00:00"))
    if beat_at is not None and beat_at.tzinfo is None:
        beat_at = beat_at.replace(tzinfo=timezone.utc)
    return (beat_at, last_status)


def _error_streak_threshold(spec: WorkerSpec) -> int:
    """B1: how many consecutive error-status beats promote to alarm.

    Daily-cadence workers get N=1 (a second error means ~24h blindness);
    everything else gets N=2 (one flaky cycle is tolerated).
    """
    if spec.max_staleness_seconds >= _DAILY_CADENCE_S:
        return ERROR_STREAK_ALARM_THRESHOLD_DAILY
    return ERROR_STREAK_ALARM_THRESHOLD_DEFAULT


def _optional_gate_env_active(spec: WorkerSpec) -> bool:
    """B3: True if the worker's feature flag makes it intentionally idle.

    Called only when the spec declares ``optional_gate_env`` — returns
    True when at least one of those vars is truthy (worker is active),
    False when none are (worker is expected_dormant).
    """
    import os
    for var in spec.optional_gate_env:
        val = os.environ.get(var, "").strip().lower()
        if val and val not in {"false", "0", "no"}:
            return True
    return False


async def check_all_workers(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, object]]:
    """Run one watchdog pass. Returns a status dict per worker."""
    statuses: list[dict[str, object]] = []
    for spec in WORKER_REGISTRY:
        if not _env_gates_met(spec):
            statuses.append({
                "name": spec.name,
                "state": "expected_absent",
                "reason": f"required_env not set: {spec.required_env}",
            })
            continue

        # Healer B3: worker is spawned but its feature flags are off →
        # intentionally idle. Classify expected_dormant BEFORE the staleness
        # / heartbeat_error checks so we never alarm on it.
        if spec.optional_gate_env and not _optional_gate_env_active(spec):
            statuses.append({
                "name": spec.name,
                "description": spec.description,
                "state": "expected_dormant",
                "reason": (
                    f"none of {spec.optional_gate_env} are truthy — "
                    "worker intentionally idle"
                ),
            })
            continue

        # FU-15: single_shot workers use a distinct state machine. They
        # record ONE heartbeat with status='single_shot_completed' on clean
        # exit; the watchdog must distinguish "successfully completed" from
        # "still inside startup grace" from "should have completed by now".
        if spec.single_shot:
            beat_at, last_status = await _fetch_heartbeat_status(
                session_factory, spec.name,
            )
            entry: dict[str, object] = {
                "name": spec.name,
                "description": spec.description,
                "stateful": spec.stateful,
                "max_staleness_seconds": spec.max_staleness_seconds,
                "single_shot": True,
                "last_status": last_status,
            }
            if beat_at is None:
                if _container_uptime_seconds() < SINGLE_SHOT_GRACE_SECONDS:
                    entry["state"] = "starting"
                else:
                    entry["state"] = "single_shot_never_completed"
            elif last_status == "single_shot_completed":
                entry["state"] = "single_shot_completed"
            else:
                # Heartbeat exists but with an unexpected status — log + alarm.
                entry["state"] = "single_shot_failed"
            statuses.append(entry)
            continue

        stale = await _staleness_seconds(session_factory, spec)
        entry = {
            "name": spec.name,
            "description": spec.description,
            "stateful": spec.stateful,
            "max_staleness_seconds": spec.max_staleness_seconds,
            "staleness_seconds": stale,
        }
        if stale is None:
            entry["state"] = "no_signal"
        elif stale == float("inf"):
            # Workers flagged pending_heartbeat haven't had record_heartbeat()
            # wired into their loop yet — their MAX(beat_at) is legitimately
            # NULL. Surface that distinctly so the watchdog doesn't false-
            # alarm 8x on every tick (would drown out a real never-beat).
            if spec.pending_heartbeat:
                entry["state"] = "pending_heartbeat"
            else:
                entry["state"] = "never_heartbeated"
        elif stale > spec.max_staleness_seconds:
            entry["state"] = "stale"
        else:
            # Healer B1: fresh beat + `last_status='error'` used to fall
            # through to state='ok' — the exact blind spot behind the
            # 2026-07-22 -> 2026-07-23 symbol_allowlist_refresh incident.
            # Only fetch last_status when staleness is healthy; a stale
            # worker's status is subsumed by the `stale` alarm above.
            beat_at, last_status = await _fetch_heartbeat_status(
                session_factory, spec.name,
            )
            entry["last_status"] = last_status
            if last_status == "error" and beat_at is not None:
                streak = _record_error_streak(spec.name, beat_at)
                entry["error_streak"] = streak
                if streak >= _error_streak_threshold(spec):
                    details = await _fetch_heartbeat_details(
                        session_factory, spec.name,
                    )
                    entry["state"] = "heartbeat_error"
                    entry["details_excerpt"] = details
                else:
                    entry["state"] = "ok"  # tolerate short streak
            else:
                _clear_error_streak(spec.name, beat_at)
                entry["state"] = "ok"
        statuses.append(entry)
    return statuses


def _record_error_streak(worker_name: str, beat_at: datetime) -> int:
    """Advance the consecutive-error counter for a worker on a NEW beat.

    Returns the current streak length. Same beat_at as last observed →
    no advance (watchdog polled twice inside a single worker cadence).
    """
    prev_beat = _LAST_SEEN_BEAT_AT.get(worker_name)
    if prev_beat is not None and prev_beat == beat_at:
        return _ERROR_STREAKS.get(worker_name, 0)
    _LAST_SEEN_BEAT_AT[worker_name] = beat_at
    streak = _ERROR_STREAKS.get(worker_name, 0) + 1
    _ERROR_STREAKS[worker_name] = streak
    return streak


def _clear_error_streak(
    worker_name: str, beat_at: datetime | None,
) -> None:
    """Reset the streak on a non-error beat OR when we can't observe."""
    if beat_at is not None:
        _LAST_SEEN_BEAT_AT[worker_name] = beat_at
    _ERROR_STREAKS.pop(worker_name, None)


async def _fetch_heartbeat_details(
    session_factory: async_sessionmaker[AsyncSession],
    worker_name: str,
) -> str | None:
    """Return the `details` JSONB column (as text) for the latest beat.

    Best-effort: on any error → None. The excerpt is truncated to 300
    chars so the alarm body stays legible.
    """
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        # CAST(...) is portable across SQLite tests and
                        # Postgres prod; `::text` is Postgres-only and
                        # breaks the SQLite fixture.
                        "SELECT CAST(details AS TEXT) FROM worker_heartbeats "
                        "WHERE worker_name = :n"
                    ),
                    {"n": worker_name},
                )
            ).first()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "watchdog: details fetch failed for %s: %s", worker_name, e,
        )
        return None
    if row is None or row[0] is None:
        return None
    text = str(row[0])
    return text[:300] + ("…" if len(text) > 300 else "")


async def _attempt_restart(name: str) -> bool:
    """Best-effort restart of a stale, non-stateful, registered worker.

    Returns True on successful respawn, False otherwise. Never raises —
    the watchdog loop must keep ticking even if the supervisor itself is
    misbehaving.
    """
    if not worker_supervisor.is_registered(name):
        return False
    try:
        return await worker_supervisor.restart(name)
    except Exception as e:  # noqa: BLE001
        log.error("watchdog: restart(%s) raised: %s", name, e)
        return False


async def _alert_if_dead(statuses: list[dict[str, object]]) -> None:
    # pending_heartbeat / single_shot_completed / starting are intentionally
    # NOT in the alert set — those workers are known-pending instrumentation
    # or single-shot tasks within their grace window, not real failures.
    dead = [s for s in statuses if s["state"] in BAD_STATES]
    if not dead:
        return

    # First pass: try to restart each non-stateful registered worker.
    # Restart actions are recorded so the operator can see them in the
    # same alert body as the staleness report.
    restart_results: dict[str, str] = {}
    for s in dead:
        name = str(s["name"])
        if s.get("stateful"):
            restart_results[name] = "ALERT-ONLY (stateful)"
            continue
        if not worker_supervisor.is_registered(name):
            # Non-stateful but not registered with the supervisor — older
            # workers that still own their own lifecycle in main.py.
            # Falls back to the alert-only path until they migrate.
            restart_results[name] = "alert (not_supervised)"
            continue
        ok = await _attempt_restart(name)
        restart_results[name] = "restarted" if ok else "restart_FAILED"

    lines = [f"watchdog: {len(dead)} worker(s) appear DEAD"]
    for s in dead:
        name = str(s["name"])
        stale_raw = s.get("staleness_seconds")
        if stale_raw == float("inf"):
            stale_str = "never"
        elif isinstance(stale_raw, (int, float)):
            stale_str = f"{int(stale_raw)}s"
        else:
            stale_str = "?"
        action = restart_results.get(name, "alert")
        # Healer B1: the heartbeat_error class carries the failing worker's
        # `details` JSONB excerpt (typically an exception message). Surface
        # it in the alarm body so the operator sees WHY it failed without a
        # round-trip to the DB.
        excerpt = s.get("details_excerpt")
        excerpt_str = f" details={excerpt}" if excerpt else ""
        lines.append(
            f"  - {name}: {s['state']} (stale={stale_str}, "
            f"max={s.get('max_staleness_seconds')}s) "
            f"action={action}{excerpt_str}",
        )
    body = "\n".join(lines)
    log.error(body)
    # If every dead worker was successfully restarted, downgrade severity
    # to "warning" so the operator's pager doesn't fire for self-healed
    # incidents. Anything left in ALERT-ONLY / restart_FAILED keeps the
    # critical severity that pages.
    healed_all = all(
        v == "restarted" for v in restart_results.values()
    ) and bool(restart_results)
    await alert_admin(body, severity="warning" if healed_all else "critical")


async def _watchdog_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    log.info(
        "worker_watchdog: starting (interval=%ds, %d workers tracked)",
        WATCHDOG_INTERVAL_SECONDS, len(WORKER_REGISTRY),
    )
    while True:
        n_dead = 0
        try:
            statuses = await check_all_workers(session_factory)
            n_dead = sum(1 for s in statuses if s["state"] in BAD_STATES)
            await _alert_if_dead(statuses)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("worker_watchdog: tick failed: %s", e)
        # Heartbeat ourselves AFTER the work so the timestamp reflects a
        # completed tick, not just an attempted one. Best-effort — a DB
        # blip on the heartbeat row must not kill the watchdog. The
        # host-level watchdog.sh reads this row to nuke-restart the
        # backend container if the in-process watchdog itself goes silent.
        await record_heartbeat(
            session_factory, WATCHDOG_HEARTBEAT_NAME,
            status="ok",
            details={"workers_tracked": len(WORKER_REGISTRY), "dead": n_dead},
        )
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


def start_worker_watchdog(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    return asyncio.create_task(_watchdog_loop(session_factory))


__all__ = [
    "WATCHDOG_INTERVAL_SECONDS",
    "check_all_workers",
    "start_worker_watchdog",
]
