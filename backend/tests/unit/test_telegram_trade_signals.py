"""SP-8 Phase J — Telegram trade signal sender + callback handler tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.telegram.signals import build_signal_payload, render_message, SignalCandidate
from app.telegram.trade_signals import (
    TelegramTradeConfig,
    handle_callback,
    send_trade_signal_message,
)


_NOW = datetime(2026, 5, 9, 14, 0, 0, tzinfo=timezone.utc)


def _candidate(**overrides) -> SignalCandidate:
    base = dict(
        signal_id="abc123",
        symbol="BTC/USDT",
        timeframe="1h",
        direction="LONG",
        entry_price=80_000.0,
        stop_loss_price=78_400.0,
        take_profit_price=83_280.0,
        confidence_pct=72,
        layer_summary={"L1": {"score": 0.85, "note": "EMAs aligned"}},
        margin_usdt=30.0,
        funding_rate_daily=-0.0002,
        chart_url="https://aji12.nagayuaj.com/tab1/BTC-USDT/1h?signal=abc123",
        sl_distance_pct=0.02,
        rr_ratio=2.05,
    )
    base.update(overrides)
    return SignalCandidate(**base)


async def _seed_signal(engine, *, signal_id: str = "abc123") -> None:
    candidate = _candidate(signal_id=signal_id)
    payload = build_signal_payload(
        candidate, rendered_at=_NOW, initial_leverage=5,
    )
    rendered = render_message(candidate, leverage=5, now=_NOW)
    payload["rendered_body"] = rendered.body
    payload["inline_keyboard"] = rendered.inline_keyboard
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals (id, user_id, symbol, direction, "
            "sent_at, payload, response, response_at, response_leverage) "
            "VALUES (:id, 1, 'BTC/USDT', 'LONG', :ts, :p, NULL, NULL, NULL)"
        ), {"id": signal_id, "ts": _NOW.isoformat(),
             "p": json.dumps(payload)})
        await s.commit()


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER)"
        ))
    return engine


def _config() -> TelegramTradeConfig:
    return TelegramTradeConfig(bot_token="bot-token-x", chat_id="123456")


# ---- send_trade_signal_message -----------------------------------------


@pytest.mark.asyncio
async def test_send_posts_to_telegram_and_records_message_id() -> None:
    engine = await _mk_engine()
    await _seed_signal(engine)

    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "ok": True, "result": {"message_id": 9999},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            mid = await send_trade_signal_message(
                s, signal_id="abc123", config=_config(), http=http,
            )
            await s.commit()
    assert mid == 9999
    assert "sendMessage" in captured["url"]
    assert captured["body"]["chat_id"] == "123456"
    assert "BTC/USDT" in captured["body"]["text"]
    assert "inline_keyboard" in captured["body"]["reply_markup"]
    # Message ID must be recorded for later edits.
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT payload FROM telegram_signals WHERE id='abc123'"
        ))).first()
    payload = json.loads(row.payload)
    assert payload["telegram_message_id"] == 9999


@pytest.mark.asyncio
async def test_send_returns_none_on_telegram_error() -> None:
    engine = await _mk_engine()
    await _seed_signal(engine)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "bad token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            mid = await send_trade_signal_message(
                s, signal_id="abc123", config=_config(), http=http,
            )
    assert mid is None


@pytest.mark.asyncio
async def test_send_returns_none_when_signal_id_unknown() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        mid = await send_trade_signal_message(
            s, signal_id="ghost", config=_config(),
        )
    assert mid is None


# ---- handle_callback ---------------------------------------------------


@pytest.mark.asyncio
async def test_callback_skip_marks_response() -> None:
    engine = await _mk_engine()
    await _seed_signal(engine)
    async with AsyncSession(engine) as s:
        out = await handle_callback(
            s, callback_data="sig:abc123:skip",
            config=_config(), now=_NOW,
        )
        await s.commit()
    assert out.action == "skip"
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert row.response == "skipped"


@pytest.mark.asyncio
async def test_callback_approve_marks_response_with_leverage() -> None:
    engine = await _mk_engine()
    await _seed_signal(engine)
    async with AsyncSession(engine) as s:
        out = await handle_callback(
            s, callback_data="sig:abc123:approve:7",
            config=_config(), now=_NOW,
        )
        await s.commit()
    assert out.action == "approve"
    assert out.leverage == 7
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response, response_leverage FROM telegram_signals "
            "WHERE id='abc123'"
        ))).first()
    assert row.response == "approved"
    assert row.response_leverage == 7


@pytest.mark.asyncio
async def test_callback_adjust_re_renders_via_edit_message() -> None:
    engine = await _mk_engine()
    await _seed_signal(engine)
    # Pre-record a fake message_id so edit fires.
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT payload FROM telegram_signals WHERE id='abc123'"
        ))).first()
        p = json.loads(row.payload)
        p["telegram_message_id"] = 7777
        await s.execute(sa.text(
            "UPDATE telegram_signals SET payload = :p WHERE id='abc123'"
        ), {"p": json.dumps(p)})
        await s.commit()

    edits: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "editMessageText" in str(req.url):
            edits.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncSession(engine) as s:
            out = await handle_callback(
                s, callback_data="sig:abc123:adjust:8",
                config=_config(), http=http, now=_NOW,
            )
            await s.commit()

    assert out.action == "adjust"
    assert out.leverage == 8
    assert len(edits) == 1
    assert edits[0]["message_id"] == 7777
    assert "8×" in edits[0]["text"]
    # Response field unchanged (user still deciding).
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert row.response is None


@pytest.mark.asyncio
async def test_callback_unknown_returns_unknown_outcome() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        out = await handle_callback(
            s, callback_data="garbage:not:valid",
            config=_config(), now=_NOW,
        )
    assert out.action == "unknown"


# ────────────────────────────────────────────────────────────────────────────
# PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE (2026-05-26)
# Race guard on approve UPDATE: prevent late approves from overwriting
# `auto_skipped` markers + creating phantom 'approved' rows that the poller
# would happily call _place_approved_order on.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_flight_approve_loses_to_auto_skip_race() -> None:
    """Auto-skip worker wrote response='auto_skipped' first; then the
    operator's approve callback arrives. The race guard
    (`WHERE response IS NULL`) must reject the late approve so the poller
    never calls _place_approved_order on an already-expired signal.
    """
    engine = await _mk_engine()
    await _seed_signal(engine)
    # Simulate auto-skip having already fired
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE telegram_signals "
            "SET response='auto_skipped', response_at=:ts "
            "WHERE id='abc123'"
        ), {"ts": _NOW.isoformat()})
        await s.commit()

    # Now the operator's approve arrives (late)
    async with AsyncSession(engine) as s:
        out = await handle_callback(
            s, callback_data="sig:abc123:approve:7",
            config=_config(), now=_NOW,
        )
        await s.commit()

    # Distinct action so the caller (_route_callback) skips order placement
    assert out.action == "approve_lost_race"
    assert out.placed_order is False
    # Row state was NOT overwritten — auto_skipped is preserved
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert row.response == "auto_skipped"


@pytest.mark.asyncio
async def test_approve_within_timeout_still_works() -> None:
    """Sanity check: approve flow continues to work the existing way when
    the row is still pending (response IS NULL)."""
    engine = await _mk_engine()
    await _seed_signal(engine)

    async with AsyncSession(engine) as s:
        out = await handle_callback(
            s, callback_data="sig:abc123:approve:5",
            config=_config(), now=_NOW,
        )
        await s.commit()

    assert out.action == "approve"
    assert out.leverage == 5
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response, response_leverage FROM telegram_signals "
            "WHERE id='abc123'"
        ))).first()
    assert row.response == "approved"
    assert row.response_leverage == 5
