"""Nightly audit chain verifier loop. Runs at 03:00 UTC.

For each iteration:
    sleep until the next 03:00 UTC
    open a session
    for each chained table:
        result = verify_chain(session, table, columns=...)
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

Column lists per table mirror what insert_with_chain saw at write time
(sources: app.core.execution.persistence, app.shadow.persistence,
app.ws.live_prediction). When new chained columns are added at write time,
they MUST be added here too — otherwise verify_chain will mis-recompute
row_hash and report a (spurious) break.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.audit_verify import verify_chain
from app.ops.alerts import alert_admin
from app.ops.heartbeat import record_heartbeat

log = logging.getLogger(__name__)

# FU-1: heartbeat name MUST match worker_registry.py's entry.
WORKER_NAME: str = "audit_verifier_task"

DEFAULT_VERIFIER_HOUR_UTC: int = 3

# Map table → list of column names that participate in the row hash.
# These MUST match the payload keys insert_with_chain saw at write time.
# Sources of truth (kept in sync by hand for now):
#   predictions   — app.ws.live_prediction.start_background_worker
#                   (calls app.core.execution.persistence.persist_prediction)
#   paper_trades  — app.core.execution.persistence.persist_trade
#   shadow_trades — app.shadow.persistence.persist_shadow_trade
CHAINED_TABLES: dict[str, list[str]] = {
    "predictions": [
        "user_id",
        "symbol",
        "timeframe",
        "ts",
        "layer_scores",
        "final_score",
        "direction",
        "confidence",
        "inputs_hash",
        "model_version",
        "cold_start",
    ],
    "paper_trades": [
        "symbol",
        "direction",
        "entry_price",
        "exit_price",
        "stop_loss",
        "take_profit",
        "position_size",
        "opened_at",
        "closed_at",
        "pnl_pct",
        "max_drawdown_during",
        "bars_held",
        "exit_reason",
        "reasoning",
        "model_version",
    ],
    "shadow_trades": [
        "user_id",
        "symbol",
        "timeframe",
        "direction",
        "entry_price",
        "stop_loss",
        "take_profit",
        "position_size_usdt",
        "entry_score",
        "entry_confidence",
        "layer_scores",
        "entry_atr",
        "exit_price",
        "exit_reason",
        "pnl_pct",
        "pnl_usdt",
        "bars_held",
        "opened_at",
        "closed_at",
        "inputs_hash",
        "model_version",
        "signal_id",
    ],
}


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
    """One-shot verifier round across every CHAINED_TABLES entry.

    Public for D4 integration tests so the test can drive a single round
    without spinning up the full :func:`run_audit_verifier_loop` time-loop.
    Errors during one table's verify are logged and swallowed so the rest
    of the round continues; asyncio.CancelledError still propagates.
    """
    async with session_factory() as session:
        for table, columns in CHAINED_TABLES.items():
            try:
                result = await verify_chain(session, table, columns=columns)
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
    "CHAINED_TABLES",
    "DEFAULT_VERIFIER_HOUR_UTC",
    "_check_all_chains",
    "run_audit_verifier_loop",
    "seconds_until_next_utc_hour",
    "start_audit_verifier_task",
]
