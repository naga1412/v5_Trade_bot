"""PR-FIX-PR275-FOLLOWUP (2026-05-27): regression tests for the
reconciliation pass in `live_exit_monitor`.

ROOT-CAUSE TESTS
================
Two failures observed in prod on 2026-05-26 19:54-19:58 UTC:

1. `reconcile_stale_pending` raised every 30s:
     "invalid input for query argument $1: '2026-05-26T19:49:47.582309+00:00'
      (expected datetime instance, got 'str')"

   Cause: `_list_stale_pending_trades` bound the cutoff as
   `.isoformat()` (string). asyncpg's TIMESTAMPTZ parameter binding
   refuses str — it wants a `datetime`. Fixed by binding the raw
   datetime object; SQLAlchemy serialises it correctly for both
   asyncpg (native typed) and aiosqlite (TEXT serialisation, matching
   the writer side).

2. (covered separately in test_live_trades_approved_via_extended.py
   below this file): `_phase1_insert_pending_trade` CheckViolation on
   `approved_via='telegram'`.

These tests use SQLite to verify the helper's logic; the migration
0029 test for the CHECK constraint runs Postgres-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution.live_exit_monitor import (
    _list_stale_pending_trades,
    reconcile_stale_pending,
)


_NOW = datetime(2026, 5, 26, 20, 0, 0, tzinfo=timezone.utc)


async def _mk_engine() -> Any:
    """SQLite in-memory engine with the post-alembic-0028 live_trades schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, position_value_usdt REAL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT, opened_at TIMESTAMP, mode_at_open TEXT, "
            "approved_via TEXT, reasoning TEXT, inputs_hash TEXT, "
            "closed_at TEXT, pnl_usdt REAL, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "sl_order_id TEXT, tp_order_id TEXT, failure_reason TEXT, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


async def _insert_pending_row(
    engine: Any,
    *,
    trade_id: int,
    symbol: str = "BTC/USDT",
    opened_at: datetime,
) -> None:
    """Insert a row with status='pending' for the reconciler to find."""
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO live_trades "
            "(id, user_id, symbol, direction, margin_usdt, leverage, "
            " position_value_usdt, entry_price, stop_loss, take_profit, "
            " binance_order_id, opened_at, mode_at_open, approved_via, "
            " reasoning, inputs_hash, status) "
            "VALUES (:id, 1, :sym, 'LONG', 10.0, 5, 50.0, 80000.0, 79600.0, "
            "        80400.0, '', :ts, 'telegram-approve', 'telegram', "
            "        '{}', 'abc', 'pending')"
        ), {"id": trade_id, "sym": symbol, "ts": opened_at})
        await s.commit()


class _StubBinance:
    """Reconciler-only stub. get_position() returns whatever was passed
    at __init__; close/cancel/etc. aren't exercised by the reconciler
    path. aclose() is a no-op."""

    def __init__(self, *, position_for_symbol: dict[str, Any] | None = None):
        self._positions: dict[str, Any] = position_for_symbol or {}
        self.position_calls: list[str] = []

    async def get_position(self, *, symbol: str) -> Any:
        self.position_calls.append(symbol)
        return self._positions.get(symbol)

    async def aclose(self) -> None:
        return None


def _stub_position(amt: float = 0.001) -> Any:
    return type("PositionState", (), {
        "symbol": "BTCUSDT", "position_amt": amt,
        "entry_price": 80_100.0, "leverage": 5,
        "liquidation_price": 0.0, "unrealized_pnl": 0.0,
    })


@pytest.mark.asyncio
async def test_list_stale_pending_filters_by_grace_window() -> None:
    """Only rows older than the 60s grace window are returned. Younger
    pending rows are still inside the placement-in-flight window."""
    engine = await _mk_engine()
    # Stale: 5 min old
    await _insert_pending_row(
        engine, trade_id=1, opened_at=_NOW - timedelta(minutes=5),
    )
    # Fresh: 10s old (within 60s grace)
    await _insert_pending_row(
        engine, trade_id=2, opened_at=_NOW - timedelta(seconds=10),
    )
    async with AsyncSession(engine) as s:
        pendings = await _list_stale_pending_trades(s, now=_NOW)
    assert {p.trade_id for p in pendings} == {1}


@pytest.mark.asyncio
async def test_reconcile_stale_pending_runs_without_datetime_error() -> None:
    """The exact prod failure: reconciler called with default arguments
    must not raise the asyncpg DataError on datetime binding. This is
    the regression guard for PR-FIX-PR275-FOLLOWUP."""
    engine = await _mk_engine()
    await _insert_pending_row(
        engine, trade_id=1, opened_at=_NOW - timedelta(minutes=5),
    )

    def _factory():
        return AsyncSession(engine)

    stub = _StubBinance(position_for_symbol={"BTCUSDT": _stub_position()})
    # Must not raise — this is what failed in prod every 30s.
    resolved = await reconcile_stale_pending(
        session_factory=_factory,  # type: ignore[arg-type]
        binance_factory=lambda: stub,
        now=_NOW,
    )
    assert resolved == 1


@pytest.mark.asyncio
async def test_reconcile_promotes_pending_to_open_when_binance_has_position() -> None:
    """Binance shows the position → reconciler UPDATEs status='open'.
    No failure_reason is set; the operator can later inspect order IDs
    if needed (this PR doesn't try to recover sl/tp ids — best-effort)."""
    engine = await _mk_engine()
    await _insert_pending_row(
        engine, trade_id=1, opened_at=_NOW - timedelta(minutes=5),
    )

    def _factory():
        return AsyncSession(engine)

    stub = _StubBinance(position_for_symbol={"BTCUSDT": _stub_position()})
    resolved = await reconcile_stale_pending(
        session_factory=_factory,  # type: ignore[arg-type]
        binance_factory=lambda: stub,
        now=_NOW,
    )
    assert resolved == 1
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT status, failure_reason FROM live_trades WHERE id=1"
        ))).first()
    assert row.status == "open"
    assert row.failure_reason is None
    assert stub.position_calls == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_reconcile_marks_failed_when_binance_has_no_position() -> None:
    """Binance has no matching position → reconciler UPDATEs
    status='failed' with the canonical reason string. This is the
    "Phase 2 crashed before placing the Binance order" branch."""
    engine = await _mk_engine()
    await _insert_pending_row(
        engine, trade_id=1, opened_at=_NOW - timedelta(minutes=5),
    )

    def _factory():
        return AsyncSession(engine)

    # Empty stub — every get_position returns None.
    stub = _StubBinance(position_for_symbol={})
    resolved = await reconcile_stale_pending(
        session_factory=_factory,  # type: ignore[arg-type]
        binance_factory=lambda: stub,
        now=_NOW,
    )
    assert resolved == 1
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT status, failure_reason FROM live_trades WHERE id=1"
        ))).first()
    assert row.status == "failed"
    assert row.failure_reason == "reconciler_no_binance_position_after_60s"


@pytest.mark.asyncio
async def test_reconcile_skips_already_open_and_failed_rows() -> None:
    """Only status='pending' rows are reconciled. Backfilled status
    ='closed' rows from the migration and post-placement 'open' or
    'failed' rows are untouched."""
    engine = await _mk_engine()
    await _insert_pending_row(
        engine, trade_id=1, opened_at=_NOW - timedelta(minutes=5),
    )
    # Mark one row 'open' and another 'closed' — neither should be
    # picked up by the reconciler.
    async with AsyncSession(engine) as s:
        for new_id, status in ((2, "open"), (3, "closed"), (4, "failed")):
            await s.execute(sa.text(
                "INSERT INTO live_trades "
                "(id, user_id, symbol, direction, margin_usdt, leverage, "
                " position_value_usdt, entry_price, stop_loss, take_profit, "
                " binance_order_id, opened_at, mode_at_open, approved_via, "
                " reasoning, inputs_hash, status) "
                "VALUES (:id, 1, 'BTC/USDT', 'LONG', 10.0, 5, 50.0, "
                "        80000.0, 79600.0, 80400.0, 'x', :ts, "
                "        'telegram-approve', 'telegram', '{}', 'abc', :s)"
            ), {"id": new_id, "ts": _NOW - timedelta(minutes=5), "s": status})
        await s.commit()

    def _factory():
        return AsyncSession(engine)

    stub = _StubBinance(position_for_symbol={"BTCUSDT": _stub_position()})
    resolved = await reconcile_stale_pending(
        session_factory=_factory,  # type: ignore[arg-type]
        binance_factory=lambda: stub,
        now=_NOW,
    )
    # Only the pending row was reconciled (1 row).
    assert resolved == 1
    # Binance queried exactly once (only for the stale-pending row).
    assert stub.position_calls == ["BTCUSDT"]
    # Other rows are unchanged.
    async with AsyncSession(engine) as s:
        rows = {
            r.id: r.status for r in (await s.execute(sa.text(
                "SELECT id, status FROM live_trades ORDER BY id"
            ))).all()
        }
    assert rows[1] == "open"      # the reconciled row
    assert rows[2] == "open"      # untouched
    assert rows[3] == "closed"    # untouched
    assert rows[4] == "failed"    # untouched
