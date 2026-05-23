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

from app.db.audit import HASH_PAYLOAD_COLUMNS
from app.db.audit_verify import VerifyResult, Violation
from app.ops.verifier_scheduler import (
    _tables_to_verify,
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
        session: AsyncSession, table: str,
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

    assert set(verify_calls) == set(HASH_PAYLOAD_COLUMNS.keys())


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
        session: AsyncSession, table: str,
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
        if len(alerts) >= len(HASH_PAYLOAD_COLUMNS):
            raise asyncio.CancelledError()

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert len(alerts) == len(HASH_PAYLOAD_COLUMNS)
    for msg, severity in alerts:
        assert severity == "critical"
        assert "audit chain broken" in msg.lower()
    assert len(inserts) == len(HASH_PAYLOAD_COLUMNS)
    assert all(rec["row_id"] == 42 for rec in inserts)
    assert {rec["table"] for rec in inserts} == set(HASH_PAYLOAD_COLUMNS.keys())


@pytest.mark.asyncio
async def test_verifier_loop_per_table_exception_does_not_abort_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verify_chain crash for one table is logged; the loop continues."""
    seen: list[str] = []

    async def _fake_verify(
        session: AsyncSession, table: str,
    ) -> VerifyResult:
        seen.append(table)
        if table == next(iter(HASH_PAYLOAD_COLUMNS)):
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
    assert set(seen) == set(HASH_PAYLOAD_COLUMNS.keys())


# ---------------------------------------------------------------------------
# PR-FU24-VERIFIER-COLUMN-DRIFT — Component A regression tests
# ---------------------------------------------------------------------------


def test_chained_tables_dict_removed() -> None:
    """Regression: the legacy `CHAINED_TABLES` dict must not come back.

    PR-FU24-VERIFIER-COLUMN-DRIFT replaced the hand-maintained per-table
    column lists with iteration over HASH_PAYLOAD_COLUMNS so writer and
    verifier stay in lockstep. A re-introduction would re-open the same
    column-drift class of bug that produced false `audit_chain_broken`
    alarms on row_id=1 every night.
    """
    import app.ops.verifier_scheduler as vs
    assert not hasattr(vs, "CHAINED_TABLES")


def test_verifier_covers_all_chained_tables() -> None:
    """Verifier iterates every table registered in HASH_PAYLOAD_COLUMNS.

    Pre-fix: 3 of 8 tables walked (predictions, paper_trades, shadow_trades).
    Post-fix: all 8 tables walked. If HASH_PAYLOAD_COLUMNS gains a new
    table later, the verifier picks it up automatically — no second
    edit point required.
    """
    tables = set(_tables_to_verify())
    assert tables == set(HASH_PAYLOAD_COLUMNS.keys())
    assert len(tables) >= 8


@pytest.mark.asyncio
async def test_verifier_calls_verify_chain_without_columns_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier must use the whitelist-native verify_chain path.

    PR-FU24-VERIFIER-COLUMN-DRIFT: passing `columns=[...]` selects the
    legacy path in verify_chain that hashes only those columns —
    guaranteed mismatch when the local list drifts from HASH_PAYLOAD_COLUMNS.
    The fix is to call verify_chain(session, table) (no `columns=`) so
    verify_chain itself derives the column set from HASH_PAYLOAD_COLUMNS.
    """
    seen_kwargs: list[dict[str, object]] = []

    async def _fake_verify(
        session: AsyncSession, table: str, **kwargs: object,
    ) -> VerifyResult:
        seen_kwargs.append(kwargs)
        return VerifyResult(ok=True, rows_checked=0)

    monkeypatch.setattr(
        "app.ops.verifier_scheduler.verify_chain", _fake_verify,
    )

    sleep_calls: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert seen_kwargs, "verify_chain was never called"
    for kw in seen_kwargs:
        assert "columns" not in kw, (
            f"verify_chain called with `columns=` (legacy path); "
            f"kwargs were {kw}"
        )


@pytest.mark.asyncio
async def test_verifier_reports_no_break_when_chain_actually_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: ok=True for every table → no alert, no auth_violations row.

    Distinct from the existing detect-break test: this one asserts the
    NEGATIVE — that a fully-intact chain does NOT trigger any alarm.
    """
    alerts: list[tuple[str, str]] = []

    async def _fake_alert(msg: str, *, severity: str = "warning") -> bool:
        alerts.append((msg, severity))
        return True

    async def _fake_verify(
        session: AsyncSession, table: str,
    ) -> VerifyResult:
        return VerifyResult(ok=True, rows_checked=5)

    monkeypatch.setattr("app.ops.verifier_scheduler.verify_chain", _fake_verify)
    monkeypatch.setattr("app.ops.verifier_scheduler.alert_admin", _fake_alert)

    sleep_calls: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    factory = await _factory_with_violations_table()

    with pytest.raises(asyncio.CancelledError):
        await run_audit_verifier_loop(
            factory,
            _sleep=_sleep,
            _now=lambda: datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

    assert alerts == []
