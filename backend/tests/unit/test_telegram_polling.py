"""SP-8 Phase J — Telegram polling worker (route + place-on-approve)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ops import telegram_polling
from app.ops.telegram_polling import (
    PollerConfig,
    _place_approved_order,
    _poll_once,
    _route_callback,
)


_NOW = datetime(2026, 5, 9, 14, 0, 0, tzinfo=timezone.utc)


def _config() -> PollerConfig:
    return PollerConfig(bot_token="bot-token", chat_id="123456")


def _cb(data: str, *, cb_id: str = "1", chat_id: str = "123456",
        from_id: str | None = None, message_id: int = 1) -> dict[str, Any]:
    """Build a callback_query payload that passes the chat-auth gate."""
    sender = from_id if from_id is not None else chat_id
    return {
        "id": cb_id, "data": data,
        "message": {"message_id": message_id, "chat": {"id": chat_id}},
        "from": {"id": sender},
    }


# ---- Stub Binance client ------------------------------------------------


class _StubBinance:
    def __init__(self, order_id: str = "ord-1") -> None:
        self._order_id = order_id
        self.placed: list[dict[str, Any]] = []

    async def place_order(
        self, *, symbol: str, side: str, quantity: float,
        leverage: int, order_type: str,
    ) -> Any:
        self.placed.append({
            "symbol": symbol, "side": side, "quantity": quantity,
            "leverage": leverage, "type": order_type,
        })
        return type("Order", (), {
            "binance_order_id": self._order_id,
            "avg_fill_price": 80_100.0,
        })

    async def aclose(self) -> None:
        return None


# ---- Engine + seed helpers ---------------------------------------------


async def _mk_engine() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE telegram_signals ("
            "id TEXT PRIMARY KEY, user_id INTEGER, symbol TEXT, "
            "direction TEXT, sent_at TEXT, payload TEXT, "
            "response TEXT, response_at TEXT, response_leverage INTEGER)"
        ))
        # Audit-chain table mirrors live_trades schema with prev_hash/row_hash.
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


async def _seed(engine: Any, signal_id: str = "abc123") -> None:
    payload = {
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": "LONG",
        "entry_price": 80_000.0, "stop_loss_price": 78_400.0,
        "take_profit_price": 83_280.0, "confidence_pct": 72,
        "margin_usdt": 30.0, "funding_rate_daily": -0.0002,
        "rendered_body": "fake body",
        "inline_keyboard": [[{"text": "approve", "callback_data": "x"}]],
        "inputs_hash": "abc",
    }
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:i, 1, 'BTC/USDT', 'LONG', :ts, :p)"
        ), {"i": signal_id, "ts": _NOW.isoformat(),
            "p": json.dumps(payload)})
        await s.commit()


# ---- _place_approved_order ---------------------------------------------


@pytest.mark.asyncio
async def test_place_approved_order_writes_live_trade() -> None:
    engine = await _mk_engine()
    await _seed(engine)
    stub = _StubBinance(order_id="bin-9")
    async with AsyncSession(engine) as s:
        order_id = await _place_approved_order(
            s, signal_id="abc123", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()
    assert order_id == "bin-9"
    assert stub.placed == [{
        "symbol": "BTCUSDT", "side": "BUY",
        "quantity": (30.0 * 5) / 80_000.0, "leverage": 5, "type": "MARKET",
    }]
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT user_id, leverage, margin_usdt, binance_order_id, "
            "approved_via FROM live_trades"
        ))).first()
    assert row.user_id == 1
    assert row.leverage == 5
    assert row.margin_usdt == 30.0
    assert row.binance_order_id == "bin-9"
    assert row.approved_via == "telegram"


@pytest.mark.asyncio
async def test_place_approved_order_returns_none_for_unknown_signal() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        order_id = await _place_approved_order(
            s, signal_id="ghost", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: _StubBinance(),
        )
    assert order_id is None


# ---- _route_callback ---------------------------------------------------


@pytest.mark.asyncio
async def test_route_sig_skip_marks_response(monkeypatch) -> None:
    engine = await _mk_engine()
    await _seed(engine)

    def _factory():
        return AsyncSession(engine)

    # Mock the Telegram answerCallbackQuery POST.
    def _http_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_handler)) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:skip"),
            config=_config(), binance_factory=None,
            use_testnet=True, user_id=1, http=http,
        )
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert row.response == "skipped"


@pytest.mark.asyncio
async def test_route_sig_approve_places_order_when_factory_present() -> None:
    engine = await _mk_engine()
    await _seed(engine)
    stub = _StubBinance(order_id="bin-42")

    def _factory():
        return AsyncSession(engine)

    def _http_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_handler)) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:approve:7", cb_id="2"),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )

    assert stub.placed and stub.placed[0]["leverage"] == 7

    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response, response_leverage FROM telegram_signals "
            "WHERE id='abc123'"
        ))).first()
        live = (await s.execute(sa.text(
            "SELECT binance_order_id FROM live_trades"
        ))).first()
    assert sig.response == "approved"
    assert sig.response_leverage == 7
    assert live.binance_order_id == "bin-42"


@pytest.mark.asyncio
async def test_route_sig_approve_skips_order_without_factory() -> None:
    engine = await _mk_engine()
    await _seed(engine)

    def _factory():
        return AsyncSession(engine)

    def _http_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_handler)) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:approve:5", cb_id="3"),
            config=_config(), binance_factory=None,
            use_testnet=True, user_id=1, http=http,
        )
    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
        live_count = (await s.execute(sa.text(
            "SELECT count(*) FROM live_trades"
        ))).scalar()
    assert sig.response == "approved"
    assert live_count == 0  # no factory -> no order placed


@pytest.mark.asyncio
async def test_route_rl_callback_forwards_to_brain_handler(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    async def _spy(*, config, callback_query, client):
        seen.append({"data": callback_query.get("data"),
                     "client": client is not None})
        return "rl_approve"

    monkeypatch.setattr(telegram_polling, "handle_brain_callback", _spy)

    engine = await _mk_engine()

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("rl_approve:42", cb_id="4"),
            config=_config(), binance_factory=None,
            use_testnet=True, user_id=1, http=http,
        )
    assert seen == [{"data": "rl_approve:42", "client": True}]


@pytest.mark.asyncio
async def test_route_rejects_callback_from_wrong_chat_id(caplog) -> None:
    """Anyone DMing the bot must NOT be able to fire approvals."""
    engine = await _mk_engine()
    await _seed(engine)
    stub = _StubBinance(order_id="should-not-fire")

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb(
                "sig:abc123:approve:5", cb_id="x",
                chat_id="999999",  # not the configured chat
            ),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )
    # Order NOT placed; row NOT updated.
    assert stub.placed == []
    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert sig.response is None
    assert any("REJECT unauthorised callback" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_route_rejects_when_sender_id_mismatches_chat_id() -> None:
    """Even when chat is right, sender must be the operator (defends
    against group chats where another member would otherwise be allowed)."""
    engine = await _mk_engine()
    await _seed(engine)
    stub = _StubBinance(order_id="should-not-fire")

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb(
                "sig:abc123:approve:5", cb_id="y",
                chat_id="123456",
                from_id="99999",  # right chat, wrong sender
            ),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )
    assert stub.placed == []


@pytest.mark.asyncio
async def test_route_unknown_callback_logged_and_dropped(caplog) -> None:
    engine = await _mk_engine()

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("garbage", cb_id="5"),
            config=_config(), binance_factory=None,
            use_testnet=True, user_id=1, http=http,
        )
    assert any("unhandled callback_data" in r.message for r in caplog.records)


# ---- _poll_once: one cycle drives an update + advances offset ----------


@pytest.mark.asyncio
async def test_poll_once_advances_offset_and_dispatches() -> None:
    engine = await _mk_engine()
    await _seed(engine)

    update = {
        "update_id": 42,
        "callback_query": _cb("sig:abc123:skip", cb_id="cb-1"),
    }
    sent: list[dict[str, Any]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        if "getUpdates" in str(req.url):
            return httpx.Response(200, json={
                "ok": True, "result": [update],
            })
        return httpx.Response(200, json={"ok": True})

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        next_offset, ok = await _poll_once(
            session_factory=_factory,  # type: ignore[arg-type]
            config=_config(), http=http, offset=0, long_poll_timeout=1,
            binance_factory=None, use_testnet=True, user_id=1,
        )
    assert ok is True
    assert next_offset == 43
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert row.response == "skipped"


@pytest.mark.asyncio
async def test_poll_once_5xx_keeps_offset_and_returns_failure() -> None:
    engine = await _mk_engine()

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        next_offset, ok = await _poll_once(
            session_factory=_factory,  # type: ignore[arg-type]
            config=_config(), http=http, offset=99, long_poll_timeout=1,
            binance_factory=None, use_testnet=True, user_id=1,
        )
    assert ok is False
    assert next_offset == 99


@pytest.mark.asyncio
async def test_poll_once_429_keeps_offset_and_returns_failure() -> None:
    engine = await _mk_engine()

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"ok": False})

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        next_offset, ok = await _poll_once(
            session_factory=_factory,  # type: ignore[arg-type]
            config=_config(), http=http, offset=10, long_poll_timeout=1,
            binance_factory=None, use_testnet=True, user_id=1,
        )
    assert ok is False
    assert next_offset == 10
