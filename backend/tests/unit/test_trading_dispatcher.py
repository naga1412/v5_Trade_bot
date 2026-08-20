"""SP-8 Phase J — execution dispatcher tests."""
from __future__ import annotations

import json
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
            "direction TEXT, sent_at TEXT, payload TEXT, response TEXT, "
            # Phase 4 Task 9 (alembic 0038): cohort tag, NOT NULL DEFAULT.
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20')"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "margin_usdt REAL, leverage INTEGER, "
            "position_value_usdt REAL, entry_price REAL, "
            "stop_loss REAL, take_profit REAL, "
            # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): the
            # original UNIQUE on binance_order_id breaks the Phase 1
            # placeholder ('' empty-string) which the new lifecycle
            # uses for all pending rows. Dropped the UNIQUE here to
            # match the production schema after alembic 0028 (audit
            # `binance_order_id` is now in NON_HASHED_ALLOW_LIST).
            "binance_order_id TEXT, opened_at TEXT, "
            "mode_at_open TEXT, approved_via TEXT, "
            "reasoning TEXT, inputs_hash TEXT, "
            "mtf_agreement INTEGER, mtf_dominant_tf TEXT, "
            "mtf_directions_json TEXT, "
            # Lifecycle columns from alembic 0028.
            "status TEXT NOT NULL DEFAULT 'pending', "
            "sl_order_id TEXT, tp_order_id TEXT, failure_reason TEXT, "
            # Phase 4 Task 9 (alembic 0038): cohort tag, NOT NULL DEFAULT.
            "symbol_source TEXT NOT NULL DEFAULT 'established_top20', "
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


# ---- Per-symbol open-position gate (safety) -----------------------------


@pytest.mark.asyncio
async def test_blocked_when_symbol_already_has_open_position() -> None:
    """Seed open live_trades on BTC/USDT for user 1, dispatch new
    BTC/USDT signal — must be blocked AND no telegram_signals row
    written (i.e. no card would go to the operator).
    """
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.execute(sa.text(
            "INSERT INTO live_trades (user_id, symbol, direction, status) "
            "VALUES (1, 'BTC/USDT', 'LONG', 'open')"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(),
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
        await s.commit()
    assert r.outcome == "blocked_symbol_position_open"
    assert "BTC/USDT" in r.detail
    async with AsyncSession(engine) as s:
        card_count = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert card_count == 0


@pytest.mark.asyncio
async def test_allowed_when_open_position_is_different_symbol() -> None:
    """ETH/USDT open must NOT block BTC/USDT — gate is per-symbol."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.execute(sa.text(
            "INSERT INTO live_trades (user_id, symbol, direction, status) "
            "VALUES (1, 'ETH/USDT', 'LONG', 'open')"
        ))
        await s.commit()
        r = await dispatch(
            s,
            proposal=_proposal(),  # BTC/USDT
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
        await s.commit()
    assert r.outcome == "sent_telegram"


@pytest.mark.asyncio
async def test_allowed_when_only_closed_row_on_symbol() -> None:
    """`status='closed'` row must not gate a fresh entry."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.execute(sa.text(
            "INSERT INTO live_trades (user_id, symbol, direction, status) "
            "VALUES (1, 'BTC/USDT', 'LONG', 'closed')"
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


# ---- chart_url (FU-45 interim fix, 2026-08-20) ---------------------------
#
# The old `/tab1/{symbol}/{timeframe}?signal={id}` shape pointed at an
# in-app deep link that was never built. Points at Binance's own futures
# chart for the symbol instead.


@pytest.mark.asyncio
async def test_chart_url_uses_binance_native_symbol_with_explicit_base() -> None:
    """proposal.chart_base_url (when set) still wins over the Settings
    default -- explicit caller value takes precedence."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        await dispatch(
            s,
            proposal=_proposal(chart_base_url="https://aji12.nagayuaj.com"),
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT payload FROM telegram_signals LIMIT 1"
        ))).first()
    payload = json.loads(row.payload)
    assert payload["chart_url"] == "https://aji12.nagayuaj.com/BTCUSDT"


@pytest.mark.asyncio
async def test_chart_url_falls_back_to_real_binance_settings_default() -> None:
    """An empty chart_base_url (the real production case -- glue.py
    never sets one) must fall back to the real Settings.CHART_BASE_URL,
    not stay blank."""
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()
        await dispatch(
            s,
            proposal=_proposal(chart_base_url=""),
            user=_user(mode="telegram-approve"),
            now=_NOW,
        )
        await s.commit()
        row = (await s.execute(sa.text(
            "SELECT payload FROM telegram_signals LIMIT 1"
        ))).first()
    payload = json.loads(row.payload)
    assert payload["chart_url"] == "https://www.binance.com/en/futures/BTCUSDT"


@pytest.mark.asyncio
async def test_pre_card_gate_fails_open_on_db_error() -> None:
    """Dispatcher pre-card gate MUST fail-open on DB error — a card is
    only a notification, the operator is still the approval step, and
    the approve-time gate fail-CLOSES. Verifies dispatch continues to
    `sent_telegram` when the per-symbol query raises.
    """
    engine = await _mk_session()
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE users SET trading_mode='telegram-approve' WHERE id=1"
        ))
        await s.commit()

    from app.trading.execution import symbol_position_gate as _spg

    async def _boom(_session, *, user_id, symbol):  # noqa: ARG001
        raise RuntimeError("simulated DB failure")

    with patch.object(_spg, "get_open_position_trade_id", _boom):
        async with AsyncSession(engine) as s:
            r = await dispatch(
                s,
                proposal=_proposal(),
                user=_user(mode="telegram-approve"),
                now=_NOW,
            )
            await s.commit()
    # Pre-card fails open → normal dispatch continues, card is written.
    assert r.outcome == "sent_telegram"


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

    # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP (2026-05-26): place_with_sltp
    # now fires 3 sequential Binance orders — MARKET entry, then
    # STOP_MARKET SL, then TAKE_PROFIT_MARKET TP. The handler returns
    # distinct orderIds keyed on the `type=` param so the assertion
    # can check the right id ended up in live_trades.binance_order_id.
    _order_seq = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/leverage" in url:
            return httpx.Response(200, json={"leverage": 10})
        # Inspect the order type to give it a deterministic id.
        if "type=STOP_MARKET" in url:
            return httpx.Response(200, json={
                **fake_order_response, "orderId": 5555, "type": "STOP_MARKET",
            })
        if "type=TAKE_PROFIT_MARKET" in url:
            return httpx.Response(200, json={
                **fake_order_response, "orderId": 6666, "type": "TAKE_PROFIT_MARKET",
            })
        return httpx.Response(200, json=fake_order_response)

    fake_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # Patch BinanceLiveClient construction so it uses our mocked http.
    real_init = None
    from app.exchanges import binance_live as bl

    real_init = bl.BinanceLiveClient.__init__

    def patched_init(self, **kwargs):
        kwargs["http"] = fake_http
        real_init(self, **kwargs)

    # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP: _place_live_order now opens
    # independent sessions for the Phase 1 INSERT and Phase 3 UPDATE.
    # Pass a session_factory bound to the test engine so those sessions
    # hit SQLite instead of the production async_sessionmaker.
    def _test_session_factory() -> AsyncSession:
        return AsyncSession(engine)

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
                session_factory=_test_session_factory,  # type: ignore[arg-type]
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
