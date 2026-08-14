"""Item 3 (2026-08-14): dispatch_decisions persistence write path.

In-memory SQLite with a CREATE TABLE shim, mirroring migration 0035 --
same pattern as tests/db/test_live_cooldowns_persistence.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.gates.entry_quality import GateVerdict
from app.db.dispatch_decisions import record_dispatch_decision


_TS = datetime(2026, 8, 14, 9, 0, 0, tzinfo=timezone.utc)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE dispatch_decisions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "symbol TEXT NOT NULL, "
            "timeframe TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "direction TEXT, "
            "final_score REAL, "
            "gate_verdicts TEXT NOT NULL, "
            "outcome TEXT NOT NULL, "
            "detail TEXT, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
    return engine


def _verdicts() -> list[GateVerdict]:
    return [
        GateVerdict("short_disabled", "pass", "short signals not disabled"),
        GateVerdict("regime", "not_evaluated", "REGIME_GATE_ENABLED=false"),
        GateVerdict("adx", "block", "adx=18.3 < 25.0 (dominant_tf=1h)"),
        GateVerdict("min_score", "not_evaluated", "direction != LONG"),
    ]


@pytest.mark.asyncio
async def test_record_dispatch_decision_writes_all_fields() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_dispatch_decision(
        factory,
        symbol="BTC/USDT", timeframe="1h", ts=_TS,
        direction="SHORT", final_score=-0.42,
        gate_verdicts=_verdicts(),
        outcome="blocked_entry_quality",
        detail="entry_quality_gate: blocked_low_trend_strength: adx=18.3 < 25.0 (dominant_tf=1h)",
    )

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT symbol, timeframe, direction, final_score, "
            "gate_verdicts, outcome, detail FROM dispatch_decisions"
        ))).first()

    assert row is not None
    assert row.symbol == "BTC/USDT"
    assert row.timeframe == "1h"
    assert row.direction == "SHORT"
    assert row.final_score == pytest.approx(-0.42)
    assert row.outcome == "blocked_entry_quality"
    assert row.detail.startswith("entry_quality_gate:")

    verdicts = json.loads(row.gate_verdicts)
    assert len(verdicts) == 4
    by_gate = {v["gate"]: v for v in verdicts}
    assert by_gate["adx"]["verdict"] == "block"
    assert "18.3" in by_gate["adx"]["reason"]
    assert by_gate["regime"]["verdict"] == "not_evaluated"


@pytest.mark.asyncio
async def test_record_dispatch_decision_null_direction_and_score() -> None:
    """Manual-mode "emitted" dispatch results may have no gate-relevant
    direction/score yet -- must write NULL, not raise."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_dispatch_decision(
        factory,
        symbol="ETH/USDT", timeframe="1h", ts=_TS,
        direction=None, final_score=None,
        gate_verdicts=_verdicts(),
        outcome="emitted", detail=None,
    )

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT direction, final_score, detail FROM dispatch_decisions"
        ))).first()
    assert row is not None
    assert row.direction is None
    assert row.final_score is None
    assert row.detail is None


@pytest.mark.asyncio
async def test_record_dispatch_decision_write_failure_never_raises(caplog) -> None:
    """No table exists -- the INSERT fails. Must not raise, and must log
    at ERROR with the distinct "dispatch_decision_write_failed" marker,
    not a generic/swallowed message."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # no CREATE TABLE
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_dispatch_decision(
        factory,
        symbol="BTC/USDT", timeframe="1h", ts=_TS,
        direction="LONG", final_score=0.5,
        gate_verdicts=_verdicts(),
        outcome="emitted", detail="ok",
    )  # must not raise

    assert any(
        "dispatch_decision_write_failed" in r.message and r.levelname == "ERROR"
        for r in caplog.records
    )
