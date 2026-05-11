"""Watchdog task — periodically checks every worker's liveness signal.

Runs every 5 minutes. For each entry in WORKER_REGISTRY:
  - Skip if any of ``required_env`` is unset (worker is "expected absent").
  - Run ``liveness_query``; compute ``now() - max(beat_at)``.
  - If staleness > ``max_staleness_seconds``: log.error + alert_admin.
  - For ``stateful=False`` workers, the watchdog ALSO emits a structured
    log line with ``action=auto_restart_candidate`` so a future
    auto-restart hook can fire. We deliberately do NOT call task.cancel()
    + respawn here — the lifespan owns the task lifecycle, and a
    watchdog-owned restart would race with it. Restart is a follow-up.

Stateful workers (live_worker, shadow_worker, liquidation_monitor,
telegram_poller) get ALERT-ONLY treatment regardless. Restarting them
risks lost open positions, duplicate orders, or vault re-init issues.

The watchdog itself never raises. A query failure for one worker is
logged and the loop continues to the next.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ops.alerts import alert_admin
from app.ops.worker_registry import WORKER_REGISTRY, WorkerSpec

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS: int = 5 * 60


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
        stale = await _staleness_seconds(session_factory, spec)
        entry: dict[str, object] = {
            "name": spec.name,
            "description": spec.description,
            "stateful": spec.stateful,
            "max_staleness_seconds": spec.max_staleness_seconds,
            "staleness_seconds": stale,
        }
        if stale is None:
            entry["state"] = "no_signal"
        elif stale == float("inf"):
            entry["state"] = "never_heartbeated"
        elif stale > spec.max_staleness_seconds:
            entry["state"] = "stale"
        else:
            entry["state"] = "ok"
        statuses.append(entry)
    return statuses


async def _alert_if_dead(statuses: list[dict[str, object]]) -> None:
    dead = [s for s in statuses if s["state"] in {"stale", "never_heartbeated"}]
    if not dead:
        return
    lines = [f"watchdog: {len(dead)} worker(s) appear DEAD"]
    for s in dead:
        action = "ALERT-ONLY (stateful)" if s.get("stateful") else "auto_restart_candidate"
        stale_raw = s.get("staleness_seconds")
        if stale_raw == float("inf"):
            stale_str = "never"
        elif isinstance(stale_raw, (int, float)):
            stale_str = f"{int(stale_raw)}s"
        else:
            stale_str = "?"
        lines.append(
            f"  - {s['name']}: {s['state']} (stale={stale_str}, "
            f"max={s.get('max_staleness_seconds')}s) action={action}",
        )
    body = "\n".join(lines)
    log.error(body)
    await alert_admin(body, severity="critical")


async def _watchdog_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    log.info(
        "worker_watchdog: starting (interval=%ds, %d workers tracked)",
        WATCHDOG_INTERVAL_SECONDS, len(WORKER_REGISTRY),
    )
    while True:
        try:
            statuses = await check_all_workers(session_factory)
            await _alert_if_dead(statuses)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("worker_watchdog: tick failed: %s", e)
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
