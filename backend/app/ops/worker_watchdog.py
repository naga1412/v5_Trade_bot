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
})

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
            entry["state"] = "ok"
        statuses.append(entry)
    return statuses


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
        lines.append(
            f"  - {name}: {s['state']} (stale={stale_str}, "
            f"max={s.get('max_staleness_seconds')}s) action={action}",
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
