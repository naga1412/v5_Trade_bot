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


@pytest.fixture(autouse=True)
def _stub_symbol_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock get_symbol_filters so tests don't hit real Binance for
    LOT_SIZE / MIN_NOTIONAL lookups. Returns step=0.001 + min_qty=0.001
    so the test fixture's (30 USDT × 5 / 80k = 0.001875) qty quantizes
    cleanly to 0.001 — caller-observable behaviour matches what BTCUSDT
    on Binance actually returns.
    """
    from app.exchanges.binance_filters import SymbolFilters

    async def _stub(symbol: str, *, use_testnet: bool, http=None):
        return SymbolFilters(
            symbol=symbol,
            step_size=0.001,
            min_qty=0.001,
            tick_size=0.10,
            min_notional=5.0,
        )

    monkeypatch.setattr(telegram_polling, "get_symbol_filters", _stub)


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
    """In-memory BinanceLiveClient stand-in.

    Records every placement call so tests can assert against ``placed``,
    ``sl_calls``, ``tp_calls``. Sensible defaults so most tests don't
    need to override:
      - market order returns ``order_id`` with avg_fill_price=80_100.0
      - SL/TP placement succeeds with predictable order IDs
      - get_position returns a stub PositionState (matches the just-
        placed market order's quantity)
      - close_position succeeds
      - cancel_order_idempotent is a no-op returning True

    Subclasses override individual methods to inject failure modes.
    """

    def __init__(self, order_id: str = "ord-1") -> None:
        self._order_id = order_id
        self.placed: list[dict[str, Any]] = []
        self.sl_calls: list[dict[str, Any]] = []
        self.tp_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.close_calls: list[dict[str, Any]] = []
        # PR-FIX-GHOST-POSITIONS: subclasses flip these flags to inject
        # failures into place_stop_loss_close / place_take_profit_close
        # while leaving the market order working.
        self.sl_should_fail: bool = False
        self.tp_should_fail: bool = False
        self.close_should_fail: bool = False
        self._next_sl_id: int = 1
        self._next_tp_id: int = 1

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
            "qty": quantity, "symbol": symbol, "side": side,
            "status": "FILLED", "raw": {},
        })

    async def place_stop_loss_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        if self.sl_should_fail:
            from app.exchanges.binance_live import OrderRejected
            raise OrderRejected(
                f"STUB sl_should_fail: STOP_MARKET({symbol}) @ {stop_price}"
            )
        sl_id = f"sl-{self._next_sl_id}"
        self._next_sl_id += 1
        self.sl_calls.append({
            "symbol": symbol, "close_side": close_side, "stop_price": stop_price,
            "binance_order_id": sl_id,
        })
        return type("Order", (), {
            "binance_order_id": sl_id, "avg_fill_price": 0.0, "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW", "raw": {},
        })

    async def place_take_profit_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        if self.tp_should_fail:
            from app.exchanges.binance_live import OrderRejected
            raise OrderRejected(
                f"STUB tp_should_fail: TAKE_PROFIT_MARKET({symbol}) @ {stop_price}"
            )
        tp_id = f"tp-{self._next_tp_id}"
        self._next_tp_id += 1
        self.tp_calls.append({
            "symbol": symbol, "close_side": close_side, "stop_price": stop_price,
            "binance_order_id": tp_id,
        })
        return type("Order", (), {
            "binance_order_id": tp_id, "avg_fill_price": 0.0, "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW", "raw": {},
        })

    async def cancel_order_idempotent(
        self, *, symbol: str, order_id: str,
    ) -> bool:
        self.cancel_calls.append({"symbol": symbol, "order_id": order_id})
        return True

    async def close_position(self, *, symbol: str) -> Any:
        if self.close_should_fail:
            from app.exchanges.binance_live import BinanceLiveError
            raise BinanceLiveError(
                f"STUB close_should_fail: close_position({symbol})"
            )
        self.close_calls.append({"symbol": symbol})
        return type("Order", (), {
            "binance_order_id": "close-1", "avg_fill_price": 80_050.0,
            "qty": 0.001, "symbol": symbol, "side": "SELL",
            "status": "FILLED", "raw": {},
        })

    async def get_position(self, *, symbol: str) -> Any:
        """Return a stub position matching the last `place_order` call.

        Reconciler tests override this to inject specific position
        states (None = no position).
        """
        if not self.placed:
            return None
        last = self.placed[-1]
        amt = last["quantity"] if last["side"] == "BUY" else -last["quantity"]
        return type("PositionState", (), {
            "symbol": last["symbol"], "position_amt": amt,
            "entry_price": 80_100.0, "leverage": last["leverage"],
            "liquidation_price": 0.0, "unrealized_pnl": 0.0,
        })

    async def fetch_mark_price(self, *, symbol: str) -> float | None:
        """Default: return None → drift check fails open in
        PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE (existing tests
        proceed exactly as before). Tests that exercise drift behavior
        use the _StubBinanceWithPrice subclass."""
        return None

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
        # PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP columns (alembic 0028, 2026-05-26):
        # status / sl_order_id / tp_order_id / failure_reason. Default
        # status='pending' so the Phase 1 INSERT lands as the expected
        # initial state without requiring the writer to set it explicitly
        # (matches what the migration's server_default would have done
        # post-promotion to NOT NULL).
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
            "status TEXT NOT NULL DEFAULT 'pending', "
            "sl_order_id TEXT, tp_order_id TEXT, failure_reason TEXT, "
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
            lambda: AsyncSession(engine), signal_id="abc123", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()
    assert order_id == "bin-9"
    # Raw qty (30 × 5) / 80k = 0.001875, but the poller quantizes to
    # BTCUSDT's stepSize (0.001) before submitting — Binance otherwise
    # rejects with -1111 "Precision is over the maximum".
    assert stub.placed == [{
        "symbol": "BTCUSDT", "side": "BUY",
        "quantity": 0.001, "leverage": 5, "type": "MARKET",
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
async def test_place_approved_order_persists_mtf_state_from_payload() -> None:
    """PR2 §6.3 R3 — telegram-approve path symmetric with auto path: when
    telegram_signals.payload carries mtf_*, those fields land on
    live_trades.mtf_*. Locks in the read-from-jsonb → coerce dict →
    write live_trades chain. The auto-path equivalent is covered by
    test_payload_builders.py; this is the telegram-side mirror."""
    engine = await _mk_engine()
    # Seed a row whose payload includes MTF state (matches what
    # build_signal_payload writes post-Phase 6).
    mtf_payload = {
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": "LONG",
        "entry_price": 80_000.0, "stop_loss_price": 78_400.0,
        "take_profit_price": 83_280.0, "confidence_pct": 72,
        "margin_usdt": 30.0, "funding_rate_daily": -0.0002,
        "rendered_body": "x", "inline_keyboard": [],
        "inputs_hash": "abc",
        "mtf_agreement": 4,
        "mtf_dominant_tf": "1h",
        "mtf_directions": {
            "5m": 1, "15m": 1, "1h": 1, "4h": 1, "1d": -1, "1w": 0,
        },
    }
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:i, 1, 'BTC/USDT', 'LONG', :ts, :p)"
        ), {"i": "mtf-sig", "ts": _NOW.isoformat(),
            "p": json.dumps(mtf_payload)})
        await s.commit()

    stub = _StubBinance(order_id="bin-mtf")
    async with AsyncSession(engine) as s:
        await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="mtf-sig", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json "
            "FROM live_trades WHERE binance_order_id = 'bin-mtf'"
        ))).first()
    assert row.mtf_agreement == 4
    assert row.mtf_dominant_tf == "1h"
    # build_live_trade_payload canonical-serialises with sort_keys=True.
    assert row.mtf_directions_json == (
        '{"15m":1,"1d":-1,"1h":1,"1w":0,"4h":1,"5m":1}'
    )


@pytest.mark.asyncio
async def test_place_approved_order_persists_null_mtf_when_payload_lacks_it() -> None:
    """Legacy / PR1 telegram_signals rows (no mtf_* keys in payload) →
    live_trades.mtf_* NULL on approve. Bit-identical to pre-PR2 state
    on those rows."""
    engine = await _mk_engine()
    await _seed(engine)  # baseline payload has no MTF keys

    stub = _StubBinance(order_id="bin-no-mtf")
    async with AsyncSession(engine) as s:
        await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="abc123", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT mtf_agreement, mtf_dominant_tf, mtf_directions_json "
            "FROM live_trades WHERE binance_order_id = 'bin-no-mtf'"
        ))).first()
    assert row.mtf_agreement is None
    assert row.mtf_dominant_tf is None
    assert row.mtf_directions_json is None


@pytest.mark.asyncio
async def test_place_approved_order_rejects_float_mtf_directions() -> None:
    """If telegram_signals.payload.mtf_directions has float values
    (downstream rounding artifact, etc.), the approve-path coerces to
    NULL rather than silently truncating to 0 — matches the gate's
    fail-open semantics on the auto path."""
    engine = await _mk_engine()
    bad_payload = {
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": "LONG",
        "entry_price": 80_000.0, "stop_loss_price": 78_400.0,
        "take_profit_price": 83_280.0, "confidence_pct": 72,
        "margin_usdt": 30.0, "funding_rate_daily": -0.0002,
        "rendered_body": "x", "inline_keyboard": [],
        "inputs_hash": "abc",
        "mtf_agreement": 4,
        "mtf_dominant_tf": "1h",
        # Floats — must not be silently truncated to 0.
        "mtf_directions": {"5m": 0.7, "1d": -0.4},
    }
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "INSERT INTO telegram_signals "
            "(id, user_id, symbol, direction, sent_at, payload) "
            "VALUES (:i, 1, 'BTC/USDT', 'LONG', :ts, :p)"
        ), {"i": "bad-mtf", "ts": _NOW.isoformat(),
            "p": json.dumps(bad_payload)})
        await s.commit()

    stub = _StubBinance(order_id="bin-bad-mtf")
    async with AsyncSession(engine) as s:
        await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="bad-mtf", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()

    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT mtf_agreement, mtf_directions_json "
            "FROM live_trades WHERE binance_order_id = 'bin-bad-mtf'"
        ))).first()
    # mtf_agreement and mtf_dominant_tf still threaded through (int + str
    # types unaffected), but mtf_directions_json is NULL — the strict
    # coercion in _place_approved_order rejected the float-valued dict.
    assert row.mtf_agreement == 4
    assert row.mtf_directions_json is None


@pytest.mark.asyncio
async def test_place_approved_order_returns_none_for_unknown_signal() -> None:
    engine = await _mk_engine()
    async with AsyncSession(engine) as s:
        order_id = await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="ghost", leverage=5, use_testnet=True,
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


# ---- Regression: PR-FIX-PR264-RESPONSE-CHECK-CONSTRAINT-AND-AUTOSKIP -----
# PR #264 shipped with three latent bugs that blocked telegram approvals in
# prod (verified 2026-05-26 03:01-03:18 UTC). Bug 3 (the order-of-operations)
# is in scope here; bugs 1 (CHECK constraint) and 2 (auto_skip datetime) are
# covered by the existing auto_skip test (updated to bind datetime) and the
# Postgres-only suite in tests/db/test_pr264_response_check_and_autoskip.py.


class _StubBinanceWithPrice(_StubBinance):
    """Subclass that returns a configurable mark price so tests can exercise
    the drift guard path (PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE).
    `mark_price=None` falls back to the parent (drift fails open)."""

    def __init__(self, *, order_id: str = "ord-1",
                 mark_price: float | None) -> None:
        super().__init__(order_id=order_id)
        self._mark_price = mark_price

    async def fetch_mark_price(self, *, symbol: str) -> float | None:
        return self._mark_price


@pytest.mark.asyncio
async def test_route_sig_approve_drift_rejection_sets_stale_price() -> None:
    """Operator's prod failure mode: drift > APPROVAL_MAX_PRICE_DRIFT_PCT
    must set response='stale_price' instead of leaving the row stuck at
    'approved' with resulted_in_trade_id=NULL.

    Verifies the session-merge fix: handle_callback writes 'approved'
    tentatively in the same session, _place_approved_order overwrites to
    'stale_price' if drift rejects, then a single commit publishes the
    final state. Previously the two operations ran in separate sessions,
    so 'approved' committed externally first and the 'stale_price'
    overwrite then failed against the CHECK constraint."""
    engine = await _mk_engine()
    await _seed(engine)  # entry_price=80_000
    # 10% drift far exceeds default 0.5% threshold
    stub = _StubBinanceWithPrice(order_id="should-not-fire", mark_price=88_000.0)

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:approve:5", cb_id="drift-1"),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )

    # No order placed.
    assert stub.placed == []
    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response, response_leverage FROM telegram_signals "
            "WHERE id='abc123'"
        ))).first()
        live = (await s.execute(sa.text(
            "SELECT count(*) AS c, MAX(status) AS status "
            "FROM live_trades"
        ))).first()
    # Critical assertions: row final state is 'stale_price', NOT 'approved'.
    assert sig.response == "stale_price"
    # PR-FIX-GHOST-POSITIONS: Phase 1 pre-INSERT creates a pending row;
    # drift rejection in Phase 2 flips it to status='failed' atomically
    # with the response='stale_price' UPDATE on telegram_signals.
    assert live.c == 1
    assert live.status == "failed"


@pytest.mark.asyncio
async def test_route_sig_approve_drift_pass_creates_trade() -> None:
    """Happy path with drift check engaged: mark_price within threshold,
    response='approved', order placed, live_trade row written. Locks in
    that the session-merge restructure did not regress the success path."""
    engine = await _mk_engine()
    await _seed(engine)  # entry_price=80_000
    # 0.1% drift, well under default 0.5% threshold
    stub = _StubBinanceWithPrice(order_id="bin-pass", mark_price=80_080.0)

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:approve:5", cb_id="pass-1"),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )

    assert stub.placed and stub.placed[0]["leverage"] == 5
    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response, response_leverage FROM telegram_signals "
            "WHERE id='abc123'"
        ))).first()
        live = (await s.execute(sa.text(
            "SELECT binance_order_id FROM live_trades"
        ))).first()
    assert sig.response == "approved"
    assert sig.response_leverage == 5
    assert live.binance_order_id == "bin-pass"


@pytest.mark.asyncio
async def test_route_sig_approve_drift_atomic_no_intermediate_approved_commit() -> None:
    """Order-of-operations regression: after my session-merge fix, the
    tentative 'approved' write from handle_callback and the 'stale_price'
    overwrite from _place_approved_order share a single session — the
    tentative state must NEVER be visible from an independent reader.

    Simulates the race by reading from a second concurrent session right
    after _route_callback returns. With the old split-session design, an
    interleaved reader could observe response='approved' even though the
    drift guard was about to reject. With the fix, only the final
    'stale_price' state is ever visible externally."""
    engine = await _mk_engine()
    await _seed(engine)
    stub = _StubBinanceWithPrice(order_id="no-order", mark_price=92_000.0)  # 15% drift

    def _factory():
        return AsyncSession(engine)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"ok": True}),
    )) as http:
        await _route_callback(
            _factory,  # type: ignore[arg-type]
            callback_query=_cb("sig:abc123:approve:5", cb_id="atomic-1"),
            config=_config(), binance_factory=lambda: stub,
            use_testnet=True, user_id=1, http=http,
        )

    # Independent reader: final state must be 'stale_price', not 'approved'.
    async with AsyncSession(engine) as s:
        sig = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='abc123'"
        ))).first()
    assert sig.response == "stale_price"
    # PR-FIX-GHOST-POSITIONS (2026-05-26): the new lifecycle pre-INSERTs
    # a `status='pending'` row in Phase 1, then UPDATEs it to
    # `status='failed'` in Phase 3 on drift rejection. So we DO expect
    # exactly one live_trades row, marked failed, with a failure_reason
    # naming the drift. No Binance order placed — drift check fires
    # before place_with_sltp.
    async with AsyncSession(engine) as s:
        live = (await s.execute(sa.text(
            "SELECT count(*) AS c, "
            "       MAX(status) AS status, "
            "       MAX(failure_reason) AS reason, "
            "       MAX(binance_order_id) AS boid "
            "FROM live_trades"
        ))).first()
    assert live.c == 1
    assert live.status == "failed"
    assert "stale_price" in (live.reason or "")
    assert live.boid == ""  # never updated past the Phase 1 placeholder
    assert not stub.placed


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


# ────────────────────────────────────────────────────────────────────────────
# PR-MAKE-APPROVAL-TIMEOUT-AND-DRIFT-CONFIGURABLE (2026-05-26)
# Auto-skip cleanup tick + drift guard at approve-time + stale_price outcome.
# ────────────────────────────────────────────────────────────────────────────


from app.ops.telegram_polling import _auto_skip_expired_signals


def _factory_for(engine):
    """async_sessionmaker-shaped callable for the auto-skip helper."""
    def _f():
        return AsyncSession(engine)
    return _f


@pytest.mark.asyncio
async def test_auto_skip_marks_expired_signals_after_timeout() -> None:
    """A signal older than `timeout_seconds` with response=NULL must be
    marked auto_skipped. A signal younger than the cutoff is untouched.
    A signal already resolved (response='approved') is also untouched."""
    engine = await _mk_engine()
    # Seed 3 rows with different sent_at and response state. Bind datetime
    # objects directly (no .isoformat()) so the SQLite text representation
    # matches what _auto_skip_expired_signals binds for the `cutoff`
    # comparison parameter — both sides use SQLAlchemy's default
    # datetime→TEXT format ('YYYY-MM-DD HH:MM:SS+TZ'). Mixed formats
    # (e.g. ISO-T on one side, space-separated on the other) lex-compare
    # incorrectly because 'T' (84) > ' ' (32) in ASCII.
    old_ts = datetime(2026, 5, 9, 13, 0, 0, tzinfo=timezone.utc)  # 1 hour old vs _NOW
    fresh_ts = datetime(2026, 5, 9, 13, 59, 0, tzinfo=timezone.utc)  # 1 min old
    async with AsyncSession(engine) as s:
        for sig_id, ts, resp in (
            ("expired-pending", old_ts, None),
            ("fresh-pending",   fresh_ts, None),
            ("already-approved", old_ts, "approved"),
        ):
            await s.execute(sa.text(
                "INSERT INTO telegram_signals "
                "(id, user_id, symbol, direction, sent_at, payload, response) "
                "VALUES (:i, 1, 'BTC/USDT', 'LONG', :ts, '{}', :r)"
            ), {"i": sig_id, "ts": ts, "r": resp})
        await s.commit()

    # Timeout = 600s (10 min). Old row (1h ago) is expired; fresh row (1m ago) is not.
    n = await _auto_skip_expired_signals(
        _factory_for(engine),  # type: ignore[arg-type]
        timeout_seconds=600, now=_NOW,
    )
    assert n == 1  # only "expired-pending" should have been updated

    async with AsyncSession(engine) as s:
        rows = {
            r.id: r.response for r in (await s.execute(sa.text(
                "SELECT id, response FROM telegram_signals ORDER BY id"
            ))).all()
        }
    assert rows["expired-pending"] == "auto_skipped"
    assert rows["fresh-pending"] is None
    assert rows["already-approved"] == "approved"


@pytest.mark.asyncio
async def test_default_values_when_env_unset() -> None:
    """Settings defaults are 600s timeout + 0.5% drift. Reload via
    `get_settings.cache_clear()` after env mutation."""
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.TELEGRAM_APPROVAL_TIMEOUT_SECONDS == 600
    assert s.APPROVAL_MAX_PRICE_DRIFT_PCT == 0.5


@pytest.mark.asyncio
async def test_env_var_override_changes_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both fields honour env-var override via Pydantic Settings."""
    from app.config import get_settings
    monkeypatch.setenv("TELEGRAM_APPROVAL_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("APPROVAL_MAX_PRICE_DRIFT_PCT", "1.5")
    get_settings.cache_clear()
    s = get_settings()
    try:
        assert s.TELEGRAM_APPROVAL_TIMEOUT_SECONDS == 1200
        assert s.APPROVAL_MAX_PRICE_DRIFT_PCT == 1.5
    finally:
        # Restore default for downstream tests
        get_settings.cache_clear()


class _StubBinanceWithPrice(_StubBinance):
    """Adds `fetch_mark_price` for drift-check tests."""

    def __init__(self, *, order_id: str = "ord-1",
                 mark_price: float | None = 80_000.0) -> None:
        super().__init__(order_id=order_id)
        self._mark_price = mark_price
        self.mark_price_calls: list[str] = []

    async def fetch_mark_price(self, *, symbol: str) -> float | None:
        self.mark_price_calls.append(symbol)
        return self._mark_price


@pytest.mark.asyncio
async def test_approve_with_acceptable_drift_places_order() -> None:
    """Mark price within 0.5% of entry → order placed as normal."""
    engine = await _mk_engine()
    await _seed(engine, "drift-ok")
    # Entry = 80_000, mark = 80_300 → drift = 0.375% < 0.5%
    stub = _StubBinanceWithPrice(order_id="bin-OK", mark_price=80_300.0)
    async with AsyncSession(engine) as s:
        order_id = await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="drift-ok", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()
    assert order_id == "bin-OK"
    # fetch_mark_price was called once for the BTCUSDT symbol
    assert stub.mark_price_calls == ["BTCUSDT"]
    # The order WAS placed
    assert len(stub.placed) == 1


@pytest.mark.asyncio
async def test_approve_with_excessive_drift_returns_stale_price() -> None:
    """Mark price has drifted >0.5% from entry → reject with stale_price,
    no Binance order placed, telegram_signals.response set to 'stale_price'."""
    engine = await _mk_engine()
    await _seed(engine, "drift-bad")
    # Pre-mark the row as 'approved' (mimicking what handle_callback wrote)
    async with AsyncSession(engine) as s:
        await s.execute(sa.text(
            "UPDATE telegram_signals SET response='approved' "
            "WHERE id='drift-bad'"
        ))
        await s.commit()

    # Entry = 80_000, mark = 81_000 → drift = 1.25% > 0.5%
    stub = _StubBinanceWithPrice(order_id="should-not-place", mark_price=81_000.0)
    async with AsyncSession(engine) as s:
        order_id = await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="drift-bad", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()
    # No order placed
    assert order_id is None
    assert stub.placed == []
    # Row flipped from 'approved' to 'stale_price'
    async with AsyncSession(engine) as s:
        row = (await s.execute(sa.text(
            "SELECT response FROM telegram_signals WHERE id='drift-bad'"
        ))).first()
    assert row.response == "stale_price"


@pytest.mark.asyncio
async def test_stale_price_outcome_persists_to_telegram_signals() -> None:
    """End-to-end: after stale_price rejection, the telegram_signals row
    carries response='stale_price' so audits + dashboards can count it."""
    engine = await _mk_engine()
    await _seed(engine, "audit-stale")
    # Drift well above threshold
    stub = _StubBinanceWithPrice(order_id="x", mark_price=82_500.0)
    async with AsyncSession(engine) as s:
        await _place_approved_order(
            lambda: AsyncSession(engine), signal_id="audit-stale", leverage=5, use_testnet=True,
            user_id=1, binance_factory=lambda: stub, now=_NOW,
        )
        await s.commit()
    async with AsyncSession(engine) as s:
        responses = [
            r.response for r in (await s.execute(sa.text(
                "SELECT response FROM telegram_signals "
                "WHERE response='stale_price'"
            ))).all()
        ]
    assert responses == ["stale_price"]
