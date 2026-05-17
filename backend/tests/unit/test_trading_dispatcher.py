"""SP-8 Phase J — execution dispatcher tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution.dispatcher import (
    SignalProposal,
    UserContext,
    dispatch,
)


_NOW = datetime(2026, 5, 9, 14, 0, 0, tzinfo=timezone.utc)


def _proposal(**overrides) -> SignalProposal:
    base = dict(
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=80_000.0,
        stop_loss_price=78_400.0,    # 2% SL
        take_profit_price=83_280.0,  # 4% TP — RR 2:1
        confidence_pct=72,
        layer_summary={"L1": {"score": 0.85, "note": "EMAs aligned"}},
        inputs_hash="abc123",
        funding_rate_daily=-0.0002,
        chart_base_url="https://aji12.nagayuaj.com",
    )
    base.update(overrides)
    return SignalProposal(**base)


def _user(**overrides) -> UserContext:
    base = dict(
        user_id=1,
        mode="manual",
        binance_api_key="kk",
        binance_api_secret="ss",
        use_testnet=True,
        portfolio_value_usdt=1000,
        successful_trades=50,
        sizing_mode="fixed",
        fixed_size_usdt=30.0,
        max_leverage_cap=10,
        max_concurrent_positions=5,
        open_positions_count=0,
    )
    base.update(overrides)
    return UserContext(**base)


async def _mk_session() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, "
            "trading_mode TEXT NOT NULL DEFAULT 'manual')"
        ))
        await conn.execute(sa.text(
            "INSERT INTO users (id, trading_mode) VALUES (1, :m)"
        ), {"m": "manual"})
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, response TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, "
            "position_value_usdt REAL, entry_price REAL, "
            "stop_loss REAL, take_profit REAL, "
            "binance_order_id TEXT UNIQUE, opened_at TEXT, "
            "mode_at_open TEXT, approved_via TEXT, "
            "reasoning TEXT, inputs_hash TEXT, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            "prev_hash TEXT, row_hash TEXT)"
        ))
    return engine


# ---- Manual mode ---------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_mode_emits_no_action() -> None:
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='manual' WHERE id=1"
        ))
        await s.commit()
        r = await dispatch(s, proposal=_proposal(), user=_user(), now=_NOW)
    assert r.outcome == "emitted"
    assert "manual mode" in r.detail.lower()


# ---- Funding-rate block --------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_blocked_when_funding_too_high_for_long() -> None:
    """Long with funding > 1%/day → kill switch trips."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(funding_rate_daily=0.02),  # 2 %/day
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
    assert r.outcome == "blocked_funding"


# ---- Max concurrent positions -------------------------------------------


@pytest.mark.asyncio
async def test_blocked_when_at_max_concurrent_positions() -> None:
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(),
            user=_user(
                mode="telegram-approve",
                open_positions_count=5, max_concurrent_positions=5,
            ),
            now=_NOW,
        )
    assert r.outcome == "blocked_max_positions"


# ---- Telegram-approve happy path ----------------------------------------


@pytest.mark.asyncio
async def test_telegram_approve_persists_signal_row() -> None:
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(),
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
        await s.commit()
    assert r.outcome == "sent_telegram"
    assert r.signal_id is not None and r.signal_id.startswith("sig_")
    assert r.leverage_chosen == 10  # 2% SL → math 40, capped at 10
    # Verify row landed.
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT id, symbol, direction, response FROM telegram_signals"
        ))).first()
    assert row is not None
    assert row.id == r.signal_id
    assert row.symbol == "BTC/USDT"
    assert row.direction == "LONG"
    assert row.response is None  # awaiting callback


# ---- Fully-auto happy path (mocks Binance) ------------------------------


@pytest.mark.asyncio
async def test_fully_auto_places_order_and_writes_live_trade() -> None:
    engine = await _mk_session()

    fake_order_response = {
        "orderId": 4242, "symbol": "BTCUSDT", "side": "BUY",
        "executedQty": "0.00375", "avgPrice": "80000",
        "status": "FILLED",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "/leverage" in str(req.url):
            return httpx.Response(200, json={"leverage": 10})
        return httpx.Response(200, json=fake_order_response)

    fake_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # Patch BinanceLiveClient construction so it uses our mocked http.
    real_init = None
    from app.exchanges import binance_live as bl

    real_init = bl.BinanceLiveClient.__init__

    def patched_init(self, **kwargs):
        kwargs["http"] = fake_http
        real_init(self, **kwargs)

    with patch.object(bl.BinanceLiveClient, "__init__", patched_init):
        async with AsyncSession(engine) as s:
            await s.execute(sa.text(
                "UPDATE users SET trading_mode='fully-auto' WHERE id=1"
            ))
            await s.commit()
            r = await dispatch(
                s,
                proposal=_proposal(),
                user=_user(mode="fully-auto"),
                now=_NOW,
            )
            await s.commit()

    await fake_http.aclose()

    assert r.outcome == "placed", r.detail
    assert r.binance_order_id == "4242"
    assert r.leverage_chosen == 10
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, symbol, leverage, binance_order_id, "
            "mode_at_open, approved_via FROM live_trades"
        ))).first()
    assert row is not None
    assert row.user_id == 1
    assert row.symbol == "BTC/USDT"
    assert row.leverage == 10
    assert row.binance_order_id == "4242"
    assert row.mode_at_open == "fully-auto"
    assert row.approved_via == "auto"


# ---- Sizing config error path -------------------------------------------


@pytest.mark.asyncio
async def test_fixed_sizing_with_no_amount_returns_error() -> None:
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(),
            user=_user(mode="telegram-approve", fixed_size_usdt=None),
            now=_NOW,
        )
    assert r.outcome == "error"
    assert "fixed sizing" in r.detail.lower()


# ---- Re-resolves mode from DB even when UserContext is stale ------------


@pytest.mark.asyncio
async def test_dispatcher_uses_current_db_mode_not_user_context() -> None:
    """If the user's mode changed between context build + dispatch (e.g.
    auto-demote fired), the dispatcher must use the FRESH value."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        # User row in DB still says 'manual'.
        # But UserContext claims fully-auto (stale).
        r = await dispatch(
            s,
            proposal=_proposal(),
            user=_user(mode="fully-auto"),  # stale
            now=_NOW,
        )
    # Mode resolved from DB = 'manual' → no action.
    assert r.outcome == "emitted"
