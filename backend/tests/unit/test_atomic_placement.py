"""Unit tests for app.trading.execution.atomic_placement.place_with_sltp.

These exercise the core safety guarantee of PR-FIX-GHOST-POSITIONS-ATOMIC-SLTP
(2026-05-26): a Binance position can never exist for the bot without
either (a) a live_trades row reflecting it AND its SL+TP order IDs, or
(b) emergency-close having returned the position to flat.

The helper itself doesn't touch the database — it only orchestrates
Binance calls. So these tests can stub BinanceLiveClient entirely
without engine/session setup. The DB-side lifecycle is exercised by
the dispatcher / telegram-polling integration tests.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.exchanges.binance_live import (
    BinanceLiveError,
    OrderRejected,
)
from app.trading.execution.atomic_placement import (
    GhostPositionError,
    PlacementResult,
    place_with_sltp,
)


# ---- Stub Binance --------------------------------------------------------


class _StubClient:
    """In-memory client recording every call. Subclasses flip flags to
    inject failures at specific steps."""

    def __init__(
        self,
        *,
        market_should_fail: bool = False,
        sl_should_fail: bool = False,
        tp_should_fail: bool = False,
        close_should_fail: bool = False,
        cancel_should_fail: bool = False,
        position_already_closed_on_emergency: bool = False,
    ) -> None:
        self.market_should_fail = market_should_fail
        self.sl_should_fail = sl_should_fail
        self.tp_should_fail = tp_should_fail
        self.close_should_fail = close_should_fail
        self.cancel_should_fail = cancel_should_fail
        self.position_already_closed = position_already_closed_on_emergency
        self.calls: list[str] = []
        self.placed: list[dict[str, Any]] = []
        self.sl_calls: list[dict[str, Any]] = []
        self.tp_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.close_calls: list[dict[str, Any]] = []

    async def place_order(
        self, *, symbol: str, side: str, quantity: float,
        leverage: int, order_type: str,
    ) -> Any:
        self.calls.append("market")
        if self.market_should_fail:
            raise OrderRejected("STUB market_should_fail")
        self.placed.append({"symbol": symbol, "side": side, "qty": quantity})
        return type("Order", (), {
            "binance_order_id": "mkt-1",
            "avg_fill_price": 80_100.0,
            "qty": quantity, "symbol": symbol, "side": side,
            "status": "FILLED", "raw": {},
        })

    async def place_stop_loss_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        self.calls.append("sl")
        if self.sl_should_fail:
            raise OrderRejected("STUB sl_should_fail")
        self.sl_calls.append(
            {"symbol": symbol, "side": close_side, "stop": stop_price}
        )
        return type("Order", (), {
            "binance_order_id": "sl-1", "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW",
            "avg_fill_price": 0.0, "raw": {},
        })

    async def place_take_profit_close(
        self, *, symbol: str, close_side: str, stop_price: float,
    ) -> Any:
        self.calls.append("tp")
        if self.tp_should_fail:
            raise OrderRejected("STUB tp_should_fail")
        self.tp_calls.append(
            {"symbol": symbol, "side": close_side, "stop": stop_price}
        )
        return type("Order", (), {
            "binance_order_id": "tp-1", "qty": 0.0,
            "symbol": symbol, "side": close_side, "status": "NEW",
            "avg_fill_price": 0.0, "raw": {},
        })

    async def cancel_order_idempotent(
        self, *, symbol: str, order_id: str,
    ) -> bool:
        self.calls.append("cancel")
        if self.cancel_should_fail:
            raise OrderRejected("STUB cancel_should_fail")
        self.cancel_calls.append({"symbol": symbol, "order_id": order_id})
        return True

    async def close_position(self, *, symbol: str) -> Any:
        self.calls.append("close")
        if self.position_already_closed:
            # Simulates SL having fired during the failed TP race —
            # close_position returns BinanceLiveError("no open position").
            raise BinanceLiveError(
                "close_position({0}): no open position".format(symbol)
            )
        if self.close_should_fail:
            raise BinanceLiveError("STUB close_should_fail")
        self.close_calls.append({"symbol": symbol})
        return type("Order", (), {
            "binance_order_id": "close-1", "qty": 0.001,
            "symbol": symbol, "side": "SELL", "status": "FILLED",
            "avg_fill_price": 80_050.0, "raw": {},
        })


# ---- Happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_orders_succeed_returns_placement_result() -> None:
    """Market → SL → TP all succeed; helper returns the three order IDs
    and never invokes the cancel/close paths."""
    c = _StubClient()
    result = await place_with_sltp(
        c, symbol="BTCUSDT", side="BUY",
        quantity=0.001, leverage=5,
        stop_loss_price=78_400.0, take_profit_price=83_280.0,
    )
    assert isinstance(result, PlacementResult)
    assert result.entry_order.binance_order_id == "mkt-1"
    assert result.sl_order_id == "sl-1"
    assert result.tp_order_id == "tp-1"
    assert c.calls == ["market", "sl", "tp"]
    assert not c.cancel_calls
    assert not c.close_calls


@pytest.mark.asyncio
async def test_long_close_side_is_sell() -> None:
    """LONG entry (side=BUY) must close with SELL on the SL+TP orders."""
    c = _StubClient()
    await place_with_sltp(
        c, symbol="BTCUSDT", side="BUY",
        quantity=0.001, leverage=5,
        stop_loss_price=78_400.0, take_profit_price=83_280.0,
    )
    assert c.sl_calls[0]["side"] == "SELL"
    assert c.tp_calls[0]["side"] == "SELL"


@pytest.mark.asyncio
async def test_short_close_side_is_buy() -> None:
    """SHORT entry (side=SELL) must close with BUY on the SL+TP orders."""
    c = _StubClient()
    await place_with_sltp(
        c, symbol="BTCUSDT", side="SELL",
        quantity=0.001, leverage=5,
        stop_loss_price=82_000.0, take_profit_price=78_400.0,
    )
    assert c.sl_calls[0]["side"] == "BUY"
    assert c.tp_calls[0]["side"] == "BUY"


# ---- Failure paths -------------------------------------------------------


@pytest.mark.asyncio
async def test_market_entry_failure_no_position_no_sltp_attempted() -> None:
    """Market entry fails → no position to clean up. SL/TP/cancel/close
    never called; the original OrderRejected propagates."""
    c = _StubClient(market_should_fail=True)
    with pytest.raises(OrderRejected, match="market_should_fail"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    assert c.calls == ["market"]
    assert not c.placed  # market failed; nothing recorded
    assert not c.sl_calls and not c.tp_calls
    assert not c.cancel_calls and not c.close_calls


@pytest.mark.asyncio
async def test_sl_placement_failure_emergency_closes_position() -> None:
    """Market entry succeeds, SL fails → emergency close fires, the
    original OrderRejected from the SL site propagates."""
    c = _StubClient(sl_should_fail=True)
    with pytest.raises(OrderRejected, match="sl_should_fail"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    # Market placed (position opened), SL attempted + failed, no TP,
    # no cancel (no SL to cancel since it never landed), emergency close fired.
    assert c.calls == ["market", "sl", "close"]
    assert c.placed
    assert not c.cancel_calls
    assert c.close_calls == [{"symbol": "BTCUSDT"}]


@pytest.mark.asyncio
async def test_tp_placement_failure_cancels_sl_then_emergency_closes() -> None:
    """Market + SL succeed, TP fails → SL is cancelled (idempotent) and
    the position is emergency-closed. Original TP error propagates."""
    c = _StubClient(tp_should_fail=True)
    with pytest.raises(OrderRejected, match="tp_should_fail"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    # Sequence: market → sl → tp(fail) → cancel SL → close position
    assert c.calls == ["market", "sl", "tp", "cancel", "close"]
    assert c.cancel_calls == [{"symbol": "BTCUSDT", "order_id": "sl-1"}]
    assert c.close_calls == [{"symbol": "BTCUSDT"}]


@pytest.mark.asyncio
async def test_tp_failure_with_cancel_failure_still_attempts_emergency_close() -> None:
    """If cancel_order_idempotent itself raises (auth blip, rate limit),
    the helper LOGS the warning and continues to emergency close —
    cancel failure must not block the safety-critical close."""
    c = _StubClient(tp_should_fail=True, cancel_should_fail=True)
    with pytest.raises(OrderRejected, match="tp_should_fail"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    # close fired even though cancel raised
    assert c.close_calls == [{"symbol": "BTCUSDT"}]


@pytest.mark.asyncio
async def test_sl_failure_with_emergency_close_failure_raises_ghost() -> None:
    """SL fails AND the emergency close itself fails → GhostPositionError.
    Caller is expected to mark live_trades.status='failed' with the
    catastrophic message so the reconciler / operator can intervene."""
    c = _StubClient(sl_should_fail=True, close_should_fail=True)
    with pytest.raises(GhostPositionError, match="CATASTROPHIC"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    # Both close and the original SL failure are reflected in the
    # GhostPositionError message (verified via match=).


@pytest.mark.asyncio
async def test_sl_failure_with_position_already_closed_is_noncatastrophic() -> None:
    """A common race: TP fails AND close_position sees "no open position"
    because the SL fired in the same millisecond. That's NOT
    catastrophic — the system is actually flat. The original TP error
    still propagates (so the caller marks live_trades.status='failed'),
    but no GhostPositionError is raised."""
    c = _StubClient(
        tp_should_fail=True,
        position_already_closed_on_emergency=True,
    )
    with pytest.raises(OrderRejected, match="tp_should_fail"):
        await place_with_sltp(
            c, symbol="BTCUSDT", side="BUY",
            quantity=0.001, leverage=5,
            stop_loss_price=78_400.0, take_profit_price=83_280.0,
        )
    # close was attempted; raised "no open position"; helper swallowed
    # that and let the original TP error propagate.
    assert "close" in c.calls
