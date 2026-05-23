"""Nightly audit chain verifier loop. Runs at 03:00 UTC.

For each iteration:
    sleep until the next 03:00 UTC
    open a session
    for each table in HASH_PAYLOAD_COLUMNS:
        result = verify_chain(session, table)   # whitelist-native path
        if not result.ok:
            log.error
            alert_admin(...)
            INSERT into auth_violations (attempted_email='system', reason=...)

Errors during one table's verify don't abort the rest of the round.
asyncio.CancelledError propagates so the loop is cancellable cleanly on
shutdown (see app.main:lifespan).

Adapted from SP-1 app.shadow.universe_refresh — the seconds_until_next_utc
helper is duplicated locally to keep the two daemons independent (changing
the universe-refresh signature must not silently shift the verifier hour).

PR-FU24-VERIFIER-COLUMN-DRIFT (2026-05-23): pre-fix this module owned a
local ``CHAINED_TABLES`` dict that duplicated the writer's column lists
by hand. The dict drifted: ``predictions`` had 11 columns here vs 19 in
``app.db.audit.HASH_PAYLOAD_COLUMNS`` (missing 8 ghost_* columns and
``model_checkpoint_id``). Every night at 03:00 UTC the verifier recomputed
hashes with the 11-column view, mismatched the 19-column stored hashes,
and emitted false ``audit_chain_broken`` alarms on row_id=1. PR-SAFETY-
BATCH-1's per-table advisory lock fixed the actual concurrent-insert race
but did nothing for these alarms because the alarms are not a race —
they are a verifier↔writer column-list mismatch. This module now iterates
``HASH_PAYLOAD_COLUMNS.keys()`` directly and calls ``verify_chain`` in its
whitelist-native mode (no ``columns=`` argument), so verifier and writer
stay in lockstep with no hand-sync.

Bonus consequence: the old hardcoded list covered only 3 of the 8 tables
registered in ``HASH_PAYLOAD_COLUMNS`` (predictions, paper_trades,
shadow_trades). The remaining 5 — ``live_trades``, ``brain_decisions``,
``tax_events``, ``mode_change_log``, ``symbol_performance_snapshots`` —
are now verified for the first time. Expect a possible first-night alarm
on any historical row whose JSONB columns drift through asyncpg round-trip;
that drift is the subject of the follow-up Component B investigation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit import HASH_PAYLOAD_COLUMNS
from app.db.audit_verify import verify_chain
from app.ops.alerts import alert_admin
from app.ops.heartbeat import record_heartbeat

log = logging.getLogger(__name__)

# FU-1: heartbeat name MUST match worker_registry.py's entry.
WORKER_NAME: str = "audit_verifier_task"

DEFAULT_VERIFIER_HOUR_UTC: int = 3


def _tables_to_verify() -> Iterable[str]:
    """Tables the nightly verifier walks.

    Single source of truth: ``HASH_PAYLOAD_COLUMNS`` in ``app.db.audit``.
    Tests that want to narrow the set (e.g. an in-memory test schema that
    only has ``predictions``) monkeypatch this function.
    """
    return tuple(HASH_PAYLOAD_COLUMNS.keys())


# Adapted from SP-1 app.shadow.universe_refresh.seconds_until_next_utc — copied
# locally rather than imported so the two daemons evolve independently. Renamed
# to seconds_until_next_utc_hour to match the SP-7 plan's API contract.
def seconds_until_next_utc_hour(hour: int, now: datetime) -> int:
    """Return integer seconds from ``now`` until the next UTC ``hour``.

    Naive datetimes are interpreted as UTC. If ``now`` already sits exactly at
    the target hour, the next occurrence is 24h away — never zero, so the
    caller never busy-loops.
    """
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    delta = target - now
    return int(delta.total_seconds())


async def _record_violation(
    session: AsyncSession, *, table: str, row_id: int,
) -> None:
    """Insert a system-level row into auth_violations.

    Reuses the SP-0.7 auth_violations table (model: app.auth.models.AuthViolation)
    with the convention attempted_email='system' for verifier-detected breaks.
    The reason is parseable: ``audit_chain_broken:<table>:<row_id>``.
    """
    await session.execute(
        sa.text(
            "INSERT INTO auth_violations (attempted_email, reason) "
            "VALUES (:e, :r)"
        ),
        {"e": "system", "r": f"audit_chain_broken:{table}:{row_id}"},
    )
    await session.commit()


async def _check_all_chains(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One-shot verifier round across every table the verifier walks.

    Public for D4 integration tests so the test can drive a single round
    without spinning up the full :func:`run_audit_verifier_loop` time-loop.
    Errors during one table's verify are logged and swallowed so the rest
    of the round continues; asyncio.CancelledError still propagates.
    """
    async with session_factory() as session:
        for table in _tables_to_verify():
            try:
                result = await verify_chain(session, table)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("audit verifier crashed for %s", table)
                continue

            if not result.ok:
                first_id = (
                    result.violations[0].row_id if result.violations else -1
                )
                log.error(
                    "audit chain BROKEN at %s row %s "
                    "(checked=%d, violations=%d)",
                    table,
                    first_id,
                    result.rows_checked,
                    len(result.violations),
                )
                await alert_admin(
                    f"Audit chain broken: {table} "
                    f"first_violation_row={first_id}",
                    severity="critical",
                )
                try:
                    await _record_violation(
                        session, table=table, row_id=first_id,
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "failed to record audit_violations row for %s", table,
                    )
            else:
                log.info(
                    "audit verifier ok: %s rows_checked=%d",
                    table,
                    result.rows_checked,
                )


async def run_audit_verifier_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wake_at_utc_hour: int = DEFAULT_VERIFIER_HOUR_UTC,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    """Run the verifier loop until cancelled.

    Each iteration sleeps until the next ``wake_at_utc_hour`` UTC, then
    invokes :func:`_check_all_chains` once. Cancellation propagates so the
    surrounding lifespan can shut us down deterministically.
    """
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)

    while True:
        wait_s = seconds_until_next_utc_hour(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))
        from app.ops import pause_state
        if await pause_state.is_paused():
            log.debug("audit_verifier: paused, skipping nightly chain check")
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="ok", details={"paused": True},
            )
            continue
        # FU-1 behavioural change: pre-FU-1 a `_check_all_chains` exception
        # propagated up and killed the task (silent — watchdog had no
        # heartbeat to read). FU-1 swallows + heartbeats with status='error'
        # so a transient DB hiccup doesn't take the verifier down for the
        # next 24h until the container restarts. Matches the pattern used
        # by every other nightly worker (news_cleanup, intermarket_cleanup).
        try:
            await _check_all_chains(session_factory)
            await record_heartbeat(
                session_factory, WORKER_NAME, status="ok",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("audit_verifier tick failed: %s", e)
            await record_heartbeat(
                session_factory, WORKER_NAME,
                status="error", details={"error": str(e)[:200]},
            )


def start_audit_verifier_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    """Spawn the verifier loop as a background task. Wired into app.main lifespan."""
    return asyncio.create_task(run_audit_verifier_loop(session_factory))


__all__ = [
    "DEFAULT_VERIFIER_HOUR_UTC",
    "_check_all_chains",
    "_tables_to_verify",
    "run_audit_verifier_loop",
    "seconds_until_next_utc_hour",
    "start_audit_verifier_task",
]
