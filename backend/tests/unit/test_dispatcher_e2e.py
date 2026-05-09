"""SP-8 Phase J — end-to-end dispatcher tests.

Exercises the full dispatcher.dispatch() routing for each of the three
trading modes against a fresh in-memory SQLite, with the BinanceLiveClient
constructor monkeypatched to a stub. Catches regressions in:

  - the mode → outcome routing (manual / telegram-approve / fully-auto)
  - the funding-rate + max-positions pre-conditions
  - the live_trades + telegram_signals row shape (hash-chained, FIFO-ready)

Sister to the unit tests in test_dispatcher.py + test_telegram_polling.py
which cover individual helpers; this file proves the helpers compose.
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution import dispatcher as dispatcher_mod
from app.trading.execution.dispatcher import (
    SignalProposal,
    UserContext,
    dispatch,
)


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER)"
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
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


async def _seed_user(engine: Any, *, mode: str) -> None:
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, :m)"
        ), {"m": mode})
        await s.commit()


def _proposal(**overrides: Any) -> SignalProposal:
    base: dict[str, Any] = dict(
        symbol="BTC/USDT", timeframe="1h", direction="LONG",
        entry_price=80_000.0, stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72, layer_summary={"L1": {"score": 0.85}},
        inputs_hash="abc", funding_rate_daily=-0.0002,
        chart_base_url="https://aji12.nagayuaj.com",
    )
    base.update(overrides)
    return SignalProposal(**base)


def _user(**overrides: Any) -> UserContext:
    base: dict[str, Any] = dict(
        user_id=1, mode="manual",
        binance_api_key="k", binance_api_secret="s",
        use_testnet=True, portfolio_value_usdt=1000,
        successful_trades=0, sizing_mode="fixed",
        fixed_size_usdt=30.0, max_leverage_cap=10,
        max_concurrent_positions=5, open_positions_count=0,
    )
    base.update(overrides)
    return UserContext(**base)


# ---- Stub Binance client -------------------------------------------------


class _StubBinance:
    instances: list["_StubBinance"] = []

    def __init__(
        self, *, api_key: str, api_secret: str, use_testnet: bool = True,
        http: Any = None,
    ) -> None:
        self._key = api_key
        self._closed = False
        _StubBinance.instances.append(self)

    async def aclose(self) -> None:
        self._closed = True

    async def place_order(
        self, *, symbol: str, side: str, quantity: float,
        leverage: int, order_type: str,
    ) -> Any:
        return type("Order", (), {
            "binance_order_id": "stub-order-1",
            "avg_fill_price": 80_100.0,
        })


@pytest.fixture(autouse=True)
def _patch_binance(monkeypatch: pytest.MonkeyPatch) -> None:
    _StubBinance.instances.clear()
    monkeypatch.setattr(
        dispatcher_mod, "BinanceLiveClient", _StubBinance,
    )


# ---- Tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_mode_emits_no_side_effects() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="manual")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()
    assert result.outcome == "emitted"
    async with AsyncSession(engine) as s:
        n_live = (await s.execute(sa.text(
            "SELECT count(*) FROM live_trades"
        ))).scalar()
        n_tg = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n_live == 0
    assert n_tg == 0
    assert _StubBinance.instances == []  # never constructed


@pytest.mark.asyncio
async def test_telegram_approve_writes_signal_row() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()
    assert result.outcome == "sent_telegram"
    assert result.signal_id and result.signal_id.startswith("sig_")
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, symbol, direction, payload "
            "FROM telegram_signals"
        ))).first()
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTC/USDT"
    assert row.direction == "LONG"
    # payload is JSON; check it carries the rendered_body + inline_keyboard
    import json
    payload = json.loads(row.payload)
    assert "rendered_body" in payload
    assert "inline_keyboard" in payload
    assert payload["entry_price"] == 80_000.0
    # No live order placed in telegram-approve mode.
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_fully_auto_places_live_order_and_writes_audit_chain() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(), user=_user(mode="fully-auto"),
        )
        await s.commit()
    assert result.outcome == "placed"
    assert result.binance_order_id == "stub-order-1"
    assert result.leverage_chosen and result.leverage_chosen >= 1

    # Stub Binance was constructed exactly once, then closed.
    assert len(_StubBinance.instances) == 1
    assert _StubBinance.instances[0]._closed is True

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, symbol, direction, margin_usdt, leverage, "
            "binance_order_id, mode_at_open, approved_via, "
            "prev_hash, row_hash FROM live_trades"
        ))).first()
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTC/USDT"
    assert row.direction == "LONG"
    assert row.margin_usdt == 30.0  # fixed sizing
    assert row.binance_order_id == "stub-order-1"
    assert row.mode_at_open == "fully-auto"
    assert row.approved_via == "auto"
    # Audit chain: prev_hash + row_hash are populated by insert_with_chain.
    assert row.prev_hash
    assert row.row_hash
    assert row.row_hash != row.prev_hash


@pytest.mark.asyncio
async def test_funding_rate_blocks_long_when_high_positive() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    # 5% daily funding paid by longs → over the 1% guard.
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(funding_rate_daily=0.05),
            user=_user(),
        )
    assert result.outcome == "blocked_funding"
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_max_concurrent_blocks_when_at_cap() -> None:
    engine = await _mk_engine()
    await _seed_user(engine, mode="fully-auto")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(),
            user=_user(open_positions_count=5, max_concurrent_positions=5),
        )
    assert result.outcome == "blocked_max_positions"
    assert _StubBinance.instances == []


@pytest.mark.asyncio
async def test_dispatch_ignores_stale_user_context_mode() -> None:
    """UserContext.mode='fully-auto' but DB mode='manual' → emit only."""
    engine = await _mk_engine()
    await _seed_user(engine, mode="manual")
    async with AsyncSession(engine) as s:
        result = await dispatch(
            s, proposal=_proposal(),
            user=_user(mode="fully-auto"),  # stale snapshot
        )
    assert result.outcome == "emitted"  # DB wins
    assert _StubBinance.instances == []
