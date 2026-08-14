"""Item 3 (2026-08-14): persisted dispatch-decision log write path.

Gate outcomes were stdout-only, which is why the ADX counterfactual
needed 65 seconds of kline replay to answer a question a 10ms indexed
query should answer instead. This module is the write side.

Fire-and-forget from `_maybe_dispatch`, using the app's global session
factory already threaded through the live-prediction worker (per the
PR #407 lesson: ad-hoc sessions in fire-and-forget writes have caused
real incidents in this codebase before).

Telemetry, not a financial ledger: the caller must invoke this AFTER
the real dispatch decision has already been made and acted on, so a
write failure here can never block or alter a trade. That said, a
write failure must be LOUD -- `log.error` with a distinct, greppable
message, never swallowed at warning/debug. This repo has spent a month
digging out silently-swallowed failures elsewhere; this module does
not add one.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from app.core.gates.entry_quality import GateVerdict

log = logging.getLogger(__name__)


async def record_dispatch_decision(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol: str,
    timeframe: str,
    ts: datetime,
    direction: str | None,
    final_score: float | None,
    gate_verdicts: list[GateVerdict],
    outcome: str,
    detail: str | None,
) -> None:
    """INSERT one row into dispatch_decisions. Never raises.

    A write failure logs at ERROR with the "dispatch_decision_write_failed"
    marker (grep-stable) rather than being swallowed -- this is telemetry
    for exactly the class of question that has cost two multi-day
    investigations (the ADX-gate argument), so a silent gap here is a
    real regression even though it can never affect a trade.
    """
    payload = json.dumps([
        {"gate": v.gate, "verdict": v.verdict, "reason": v.reason}
        for v in gate_verdicts
    ])
    try:
        async with session_factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO dispatch_decisions "
                    "(symbol, timeframe, ts, direction, final_score, "
                    "gate_verdicts, outcome, detail) "
                    "VALUES (:sym, :tf, :ts, :dir, :fs, :gv, :oc, :det)"
                ),
                {
                    "sym": symbol, "tf": timeframe, "ts": ts,
                    "dir": direction, "fs": final_score,
                    "gv": payload, "oc": outcome, "det": detail,
                },
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 — must never raise into the dispatch path
        log.error(
            "dispatch_decision_write_failed: symbol=%s timeframe=%s ts=%s "
            "outcome=%s -- %s",
            symbol, timeframe, ts, outcome, e,
        )


__all__ = ["record_dispatch_decision"]
