"""app.healer.findings.record_finding — direct persistence coverage.

2026-08-14 remediation work order B3: the real INSERT used
`CAST(:p AS JSONB)`, which is correct Postgres SQL but silently
corrupted the JSON payload to the string '0' on SQLite (unrecognized
type name -> NUMERIC affinity -> a non-numeric string casts to 0).
This was a known, documented gap (see
tests/healer/test_c1_selftest.py's hand-seeded-row workaround) that
every test exercising healer findings on SQLite had to route around.
Fixed by binding the JSON string directly (no CAST) — works on both
dialects. This file proves the fix on SQLite; Postgres compatibility
is unchanged (same no-CAST pattern already used for shadow_trades/
predictions JSONB columns elsewhere in this codebase).
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.healer.findings import record_finding


async def _mk_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE healer_findings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "detector_name TEXT NOT NULL, "
            "detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "severity TEXT NOT NULL, "
            "summary TEXT NOT NULL, "
            "details TEXT)"
        ))
    return engine


@pytest.mark.asyncio
async def test_record_finding_details_round_trips_on_sqlite() -> None:
    """The exact regression: details must NOT collapse to '0'."""
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_finding(
        factory,
        detector_name="C5_system_truth",
        severity="warning",
        summary="test finding",
        details={"key": "table.col", "flags": ["CONSTANT"]},
    )

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT detector_name, severity, summary, details FROM healer_findings"
        ))).first()

    assert row is not None
    assert row.detector_name == "C5_system_truth"
    assert row.severity == "warning"
    assert row.details != "0"  # the regression this test guards against
    parsed = json.loads(row.details)
    assert parsed == {"key": "table.col", "flags": ["CONSTANT"]}


@pytest.mark.asyncio
async def test_record_finding_details_none_writes_null() -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_finding(
        factory, detector_name="x", severity="info", summary="clean sweep",
        details=None,
    )

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT details FROM healer_findings"
        ))).first()
    assert row is not None
    assert row.details is None


@pytest.mark.asyncio
async def test_record_finding_rejects_unknown_severity(caplog) -> None:
    engine = await _mk_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    await record_finding(
        factory, detector_name="x", severity="apocalyptic", summary="x",
    )

    async with factory() as session:
        row = (await session.execute(sa.text(
            "SELECT count(*) AS n FROM healer_findings"
        ))).first()
    assert row.n == 0
    assert any("unknown severity" in r.message for r in caplog.records)
