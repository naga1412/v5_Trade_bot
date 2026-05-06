"""Unit tests for the audit verifier scheduler — Phase D2.

Validates:
    1. seconds_until_next_utc_hour math (now → next 03:00 UTC)
    2. Loop calls verify_chain for every chained table once per iteration,
       cancellation propagates cleanly
    3. On verify_chain.ok=False the loop calls alert_admin AND writes an
       audit_violations row (via _record_violation)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.audit_verify import VerifyResult, Violation
from app.ops.verifier_scheduler import (
    CHAINED_TABLES,
    run_audit_verifier_loop,
    seconds_until_next_utc_hour,
)


def test_seconds_until_next_utc_hour_basic() -> None:
    now = datetime(2025, 1, 1, 1, 30, tzinfo=timezone.utc)
    assert seconds_until_next_utc_hour(3, now) == 90 * 60  # 1.5h

    # Exactly at the target hour → wraps to next day (never zero, never busy-loops)
    now2 = datetime(2025, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next_utc_hour(3, now2) == 24 * 3600

    # Naive datetime is interpreted as UTC.
    now3 = datetime(2025, 1, 1, 1, 30)
    assert seconds_until_next_utc_hour(3, now3) == 90 * 60


async def _factory_with_violations_table() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE auth_violations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "attempted_email TEXT NOT NULL, "
            "attempted_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "reason TEXT NOT NULL, "
            "jwt_sub TEXT, request_path TEXT)"
        ))
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_verifier_loop_calls_verify_chain_for_each_chained_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop runs one iteration then exits via injected _sleep that raises."""
    sleep_calls: list[float] = []
    verify_calls: list[str] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:  # exit after the 1st verify round
            raise asyncio.CancelledError()

    async def _fake_verify(
        session: AsyncSession, table: str, *, columns: list[str],
    ) -> VerifyResult:
        verify_calls.append(table)
        return VerifyResult(ok=True, rows_checked=10)

    monkeypatch.setattr(
        "app.ops.verifier_scheduler.verify_chain", _fake_verify,
    )

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert set(verify_calls) == set(CHAINED_TABLES.keys())


@pytest.mark.asyncio
async def test_verifier_loop_alerts_and_records_violation_on_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When verify_chain returns ok=False, alert_admin is called + row written."""
    alerts: list[tuple[str, str]] = []
    inserts: list[dict[str, Any]] = []

    async def _fake_alert(msg: str, *, severity: str = "warning") -> bool:
        alerts.append((msg, severity))
        return True

    async def _fake_verify(
        session: AsyncSession, table: str, *, columns: list[str],
    ) -> VerifyResult:
        return VerifyResult(
            ok=False,
            rows_checked=5,
            violations=[Violation(row_id=42, expected="abc", actual="xyz")],
        )

    async def _fake_record(
        session: AsyncSession, *, table: str, row_id: int,
    ) -> None:
        inserts.append({"table": table, "row_id": row_id})

    monkeypatch.setattr("app.ops.verifier_scheduler.verify_chain", _fake_verify)
    monkeypatch.setattr("app.ops.verifier_scheduler.alert_admin", _fake_alert)
    monkeypatch.setattr(
        "app.ops.verifier_scheduler._record_violation", _fake_record,
    )

    async def _sleep(s: float) -> None:
        # Exit once we've alerted on every chained table once.
        if len(alerts) >= len(CHAINED_TABLES):
            raise asyncio.CancelledError()

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert len(alerts) == len(CHAINED_TABLES)
    for msg, severity in alerts:
        assert severity == "critical"
        assert "audit chain broken" in msg.lower()
    assert len(inserts) == len(CHAINED_TABLES)
    assert all(rec["row_id"] == 42 for rec in inserts)
    assert {rec["table"] for rec in inserts} == set(CHAINED_TABLES.keys())


@pytest.mark.asyncio
async def test_verifier_loop_per_table_exception_does_not_abort_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verify_chain crash for one table is logged; the loop continues."""
    seen: list[str] = []

    async def _fake_verify(
        session: AsyncSession, table: str, *, columns: list[str],
    ) -> VerifyResult:
        seen.append(table)
        if table == next(iter(CHAINED_TABLES)):
            raise RuntimeError("boom")
        return VerifyResult(ok=True, rows_checked=3)

    monkeypatch.setattr("app.ops.verifier_scheduler.verify_chain", _fake_verify)

    sleep_count = [0]

    async def _sleep(s: float) -> None:
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError()

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    # Every chained table was attempted, even after the first one raised.
    assert set(seen) == set(CHAINED_TABLES.keys())
