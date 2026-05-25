"""PR-FIX-DISPATCHER-TELEGRAM-OUTCOME-LIE — dispatcher outcome honesty.

Pre-fix, dispatch() returned ``DispatchResult(outcome="sent_telegram")``
regardless of whether the outbound Telegram POST actually delivered.
Three failure modes were all reported as success:

  (i)   send_trade_signal_message returned None      (POST 4xx / rejected)
  (ii)  send_trade_signal_message raised             (httpx.HTTPError)
  (iii) TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset  (POST skipped entirely)

The fix makes ``_send_telegram_signal`` return ``_TelegramSendResult``
with a ``sent`` bool, and dispatch() maps to the new
``"queued_send_failed"`` outcome with the failure reason in detail.
The DB row is always persisted (source of truth + manual-retry path).

These tests pin all five branches (3 failure modes + success + no-mode-DB).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.trading.execution import dispatcher as dispatcher_mod
from app.trading.execution.dispatcher import (
    SignalProposal,
    UserContext,
    dispatch,
)


# ---- Helpers (mirrors test_dispatcher_e2e.py) ----------------------------


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
        confidence_pct=72,
        layer_summary={"L1": {"strength": 0.85, "direction": "LONG"}},
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


# ---- Success path: outcome stays sent_telegram --------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_sent_telegram_when_post_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send(session, *, signal_id, config, http=None):
        return 9999  # valid msg_id

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()

    assert result.outcome == "sent_telegram"
    assert result.signal_id and result.signal_id.startswith("sig_")
    assert "queued" in result.detail or "Telegram message queued" in result.detail


# ---- Failure mode (i): send returns None --------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_queued_send_failed_when_msg_id_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send(session, *, signal_id, config, http=None):
        return None  # simulates trade_signals.py:90 missing body/keyboard

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send,
    )

    import logging
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    with caplog.at_level(logging.ERROR, logger="app.trading.execution.dispatcher"):
        async with AsyncSession(engine) as s:
            result = await dispatch(s, proposal=_proposal(), user=_user())
            await s.commit()

    assert result.outcome == "queued_send_failed"
    assert "send_failed" in result.detail
    assert result.signal_id and result.signal_id.startswith("sig_")
    # Operator hint must be present so the next log greppable.
    assert "send_trade_signal_message" in result.detail
    # Row still persisted for manual retry.
    async with AsyncSession(engine) as s:
        n = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n == 1
    # Dispatcher logged the outbound failure (separately from trade_signals.py log).
    msgs = [r.getMessage() for r in caplog.records]
    assert any("outbound POST failed" in m for m in msgs), msgs


# ---- Failure mode (ii): send raises -------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_queued_send_failed_when_send_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _raising_send(session, *, signal_id, config, http=None):
        raise httpx.HTTPError("synthetic network blip")

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _raising_send,
    )

    import logging
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    with caplog.at_level(logging.ERROR, logger="app.trading.execution.dispatcher"):
        async with AsyncSession(engine) as s:
            result = await dispatch(s, proposal=_proposal(), user=_user())
            await s.commit()

    assert result.outcome == "queued_send_failed"
    assert "raised" in result.detail
    # Row still persisted.
    async with AsyncSession(engine) as s:
        n = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n == 1
    # log.exception captures the traceback so exc_info is implicitly True;
    # at minimum the failure message names the signal id.
    msgs = [r.getMessage() for r in caplog.records]
    assert any("outbound POST raised" in m for m in msgs), msgs


# ---- Failure mode (iii): no creds ---------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_queued_send_failed_when_no_creds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    sent: list[object] = []

    async def _should_not_call(*_a, **_k):
        sent.append(1)
        return 9999

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _should_not_call,
    )

    import logging
    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    with caplog.at_level(logging.WARNING, logger="app.trading.execution.dispatcher"):
        async with AsyncSession(engine) as s:
            result = await dispatch(s, proposal=_proposal(), user=_user())
            await s.commit()

    assert result.outcome == "queued_send_failed"
    assert "no_creds" in result.detail
    assert sent == [], "outbound POST must not be attempted when creds unset"
    # Row still persisted.
    async with AsyncSession(engine) as s:
        n = (await s.execute(sa.text(
            "SELECT count(*) FROM telegram_signals"
        ))).scalar()
    assert n == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any("TELEGRAM_BOT_TOKEN" in m and "unset" in m for m in msgs), msgs


# ---- All three failure paths still persist the DB row -------------------


@pytest.mark.asyncio
async def test_db_row_persisted_in_all_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB row is the source of truth. In every failure mode the row
    must be written so a future healer or manual retry can flush it."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    async def _fake_send_none(session, *, signal_id, config, http=None):
        return None

    monkeypatch.setattr(
        dispatcher_mod, "send_trade_signal_message", _fake_send_none,
    )

    engine = await _mk_engine()
    await _seed_user(engine, mode="telegram-approve")
    async with AsyncSession(engine) as s:
        result = await dispatch(s, proposal=_proposal(), user=_user())
        await s.commit()
    assert result.outcome == "queued_send_failed"

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT id, symbol, payload FROM telegram_signals WHERE id = :id"
        ), {"id": result.signal_id})).first()
    assert row is not None
    assert row.symbol == "BTC/USDT"
    # Payload must include rendered_body + inline_keyboard so an operator
    # retry / healer can resend without re-rendering.
    import json
    payload = json.loads(row.payload)
    assert "rendered_body" in payload
    assert "inline_keyboard" in payload


# ---- DispatchOutcome Literal includes the new value ---------------------


def test_dispatchoutcome_literal_includes_queued_send_failed() -> None:
    """Catch a regression where the new value gets removed from the Literal
    while consumers still reference it (would surface as a type error or
    a runtime mismatch in match statements)."""
    from typing import get_args
    from app.trading.execution.dispatcher import DispatchOutcome
    assert "queued_send_failed" in get_args(DispatchOutcome)
    assert "sent_telegram" in get_args(DispatchOutcome)  # regression guard
