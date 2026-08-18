"""PR-FIX-PR275-PAYLOAD-STATUS (2026-05-27): regression test for the
status-default trap.

ROOT CAUSE
==========
Migration 0028 set ``live_trades.status NOT NULL DEFAULT 'closed'`` to
make legacy backfilled rows terminal-safe. But `build_live_trade_payload`
DID NOT include `status` in its output dict, so new INSERTs via
`_phase1_insert_pending_trade` silently inherited the column default
('closed'). The Phase 1 SELECT-by-placeholder filtered on
`status = 'pending'` → no match → "INSERT succeeded but row lookup
failed" → `_place_approved_order` returned None → no Binance order
placed (good) but the row was stuck in 'closed' state with a
`pending-{signal_id}` placeholder for binance_order_id (bad — confused
audit + operator).

The other test SQLite schemas use `DEFAULT 'pending'` (the lifecycle
convention) which masked this bug from CI. THIS file uses
`DEFAULT 'closed'` to mirror prod and catch the same class of bug if
it returns.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ops.telegram_polling import _place_approved_order


_NOW = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)


class _StubBinance:
    """Minimal Binance stub — just enough for _place_approved_order to
    reach Phase 3 and complete the lifecycle."""

    def __init__(self) -> None:
        self.placed: list[dict] = []

    async def place_order(
        self, *, symbol: str, side: str, quantity: float,
        leverage: int, order_type: str,
    ) -> Any:
        self.placed.append({"symbol": symbol, "side": side, "qty": quantity})
        return type("Order", (), {
            "binance_order_id": "stub-entry-1",
            "avg_fill_price": 80_100.0,
            "qty": quantity, "symbol": symbol, "side": side,
            "status": "FILLED", "raw": {},
        })

    async def place_stop_loss_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        return type("Order", (), {
            "binance_order_id": "stub-sl-1", "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW",
            "avg_fill_price": 0.0, "raw": {},
        })

    async def place_take_profit_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        return type("Order", (), {
            "binance_order_id": "stub-tp-1", "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW",
            "avg_fill_price": 0.0, "raw": {},
        })

    async def fetch_mark_price(self, *, symbol: str) -> float | None:
        return 80_050.0  # within drift threshold

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_symbol_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exchanges.binance_filters import SymbolFilters
    from app.ops import telegram_polling

    async def _stub(symbol: str, *, use_testnet: bool, http=None):
        return SymbolFilters(
            symbol=symbol, step_size=0.001, min_qty=0.001,
            tick_size=0.10, min_notional=5.0,
        )
    monkeypatch.setattr(telegram_polling, "get_symbol_filters", _stub)


async def _mk_engine_with_prod_default() -> Any:
    """SQLite schema mirroring prod alembic 0028 default of 'closed'.

    Other test files use DEFAULT 'pending' (the lifecycle value), which
    masks the status-omitted bug. This file uses 'closed' so the test
    fails loudly if `build_live_trade_payload` ever stops emitting an
    explicit `status` value.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER, "
            # Phase 4 Task 9 (alembic 0038): cohort tag, NOT NULL DEFAULT.
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, position_value_usdt REAL, "
            "entry_price REAL, stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT, opened_at TEXT, mode_at_open TEXT, "
            "approved_via TEXT, reasoning TEXT, inputs_hash TEXT, "
            "closed_at TEXT, pnl_usdt REAL, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            # The prod default ('closed') — this is the trap the fix
            # has to override by writing status explicitly.
            "status TEXT NOT NULL DEFAULT 'closed', "
            "sl_order_id TEXT, tp_order_id TEXT, failure_reason TEXT, "
            # Phase 4 Task 9 (alembic 0038): cohort tag, NOT NULL DEFAULT.
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


async def _seed_signal(engine: Any, signal_id: str) -> None:
    payload = {
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": "LONG",
        "entry_price": 80_000.0, "stop_loss_price": 79_600.0,
        "take_profit_price": 80_400.0, "confidence_pct": 80,
        "margin_usdt": 10.0, "funding_rate_daily": 0.0,
        "rendered_body": "", "inline_keyboard": [],
        "inputs_hash": "regression_test",
    }
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:i, 1, 'BTC/USDT', 'LONG', :ts, :p)"
        ), {"i": signal_id, "ts": _NOW.isoformat(), "p": json.dumps(payload)})
        await s.commit()


@pytest.mark.asyncio
async def test_phase1_insert_writes_pending_against_prod_default_closed() -> None:
    """The exact prod failure: with `status NOT NULL DEFAULT 'closed'`
    on the column, `_place_approved_order` MUST write `status='pending'`
    explicitly so the Phase 1 SELECT-by-placeholder finds the row.

    Pre-fix this returned None; post-fix it returns the entry order id."""
    engine = await _mk_engine_with_prod_default()
    sig_id = "sig_regression_pr275_status_default"
    await _seed_signal(engine, sig_id)

    stub = _StubBinance()
    order_id = await _place_approved_order(
        lambda: AsyncSession(engine),  # type: ignore[arg-type]
        signal_id=sig_id, leverage=20, use_testnet=True,
        user_id=1, binance_factory=lambda: stub, now=_NOW,
    )
    assert order_id == "stub-entry-1", (
        f"_place_approved_order returned {order_id!r}; expected the entry "
        f"order id. None indicates Phase 1 lookup failed (the pre-fix "
        f"symptom) — check build_live_trade_payload includes status='pending'."
    )

    # Verify the row landed with the correct lifecycle state.
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT status, binance_order_id, sl_order_id, tp_order_id "
            "FROM live_trades ORDER BY id DESC LIMIT 1"
        ))).first()
    assert row.status == "open", f"row status={row.status} (expected 'open')"
    assert row.binance_order_id == "stub-entry-1"
    assert row.sl_order_id == "stub-sl-1"
    assert row.tp_order_id == "stub-tp-1"


@pytest.mark.asyncio
async def test_build_live_trade_payload_always_emits_status_pending() -> None:
    """Direct assertion on the builder: `status='pending'` is in the
    output unless explicitly overridden. The 'closed' DB default never
    applies because the INSERT carries an explicit value."""
    from app.db.payload_builders import build_live_trade_payload

    payload = build_live_trade_payload(
        user_id=1, symbol="BTC/USDT", direction="LONG",
        margin_usdt=10.0, leverage=20,
        entry_price=80_000.0, stop_loss=79_600.0, take_profit=80_400.0,
        binance_order_id="pending-sig_test",
        opened_at=_NOW, mode_at_open="telegram-approve",
        approved_via="telegram",
        reasoning_json="{}", inputs_hash="abc",
    )
    assert payload["status"] == "pending"

    # Operator can override (e.g. for a backfill / repair script):
    payload_override = build_live_trade_payload(
        user_id=1, symbol="BTC/USDT", direction="LONG",
        margin_usdt=10.0, leverage=20,
        entry_price=80_000.0, stop_loss=79_600.0, take_profit=80_400.0,
        binance_order_id="repair-1",
        opened_at=_NOW, mode_at_open="manual",
        approved_via="auto",
        reasoning_json="{}", inputs_hash="abc",
        status="closed",
    )
    assert payload_override["status"] == "closed"
