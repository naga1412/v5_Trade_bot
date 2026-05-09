"""SP-8 Phase J — liquidation monitor tests."""
from __future__ import annotations


import httpx
import pytest

from app.exchanges.binance_live import (
    BinanceLiveClient,
    PositionState,
)
from app.trading.execution.liquidation_monitor import (
    OpenPosition,
    evaluate_position,
)


def _pos(**overrides) -> OpenPosition:
    base = dict(
        trade_id=42, user_id=1,
        symbol="BTC/USDT", direction="LONG",
        entry_price=80_000.0, stop_loss_price=78_400.0,
    )
    base.update(overrides)
    return OpenPosition(**base)


def _client_with_position(state: PositionState | None) -> BinanceLiveClient:
    """Build a BinanceLiveClient whose get_position returns ``state``."""

    def handler(req: httpx.Request) -> httpx.Response:
        if state is None:
            return httpx.Response(200, json=[{
                "symbol": "BTCUSDT", "positionAmt": "0",
                "entryPrice": "0", "leverage": "1",
                "liquidationPrice": "0", "unRealizedProfit": "0",
            }])
        return httpx.Response(200, json=[{
            "symbol": state.symbol,
            "positionAmt": str(state.position_amt),
            "entryPrice": str(state.entry_price),
            "leverage": str(state.leverage),
            "liquidationPrice": str(state.liquidation_price),
            "unRealizedProfit": str(state.unrealized_pnl),
        }])
    return BinanceLiveClient(
        api_key="k", api_secret="s",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ---- Healthy: buffer above threshold ------------------------------------


@pytest.mark.asyncio
async def test_healthy_position_returns_ok() -> None:
    state = PositionState(
        symbol="BTCUSDT", position_amt=0.05,
        entry_price=80_000, leverage=10,
        liquidation_price=72_000,  # 10% below entry
        unrealized_pnl=0,
    )
    client = _client_with_position(state)
    try:
        out = await evaluate_position(pos=_pos(), binance=client)
    finally:
        await client.aclose()
    assert out.action == "ok"


# ---- Already closed externally ------------------------------------------


@pytest.mark.asyncio
async def test_no_position_on_binance_returns_ok_with_note() -> None:
    client = _client_with_position(None)
    try:
        out = await evaluate_position(pos=_pos(), binance=client)
    finally:
        await client.aclose()
    assert out.action == "ok"
    assert "no longer open" in out.detail


# ---- Warning zone: alert but don't close --------------------------------


@pytest.mark.asyncio
async def test_alert_when_buffer_below_threshold_but_above_auto_close() -> None:
    """Original distance entry$80k -> liq$72k = $8k. Current price $77k →
    distance $5k, buffer 5/8 = 62.5% — fine.
    Drop current to $74k → distance $2k, buffer 2/8 = 25% < 50% threshold,
    > 10% auto-close → alert action."""
    state = PositionState(
        symbol="BTCUSDT", position_amt=0.05,
        entry_price=74_000,  # current proxy
        leverage=10,
        liquidation_price=72_000,
        unrealized_pnl=-100,
    )
    client = _client_with_position(state)
    notifications: list[str] = []

    async def notify(msg: str) -> None:
        notifications.append(msg)

    try:
        out = await evaluate_position(
            pos=_pos(entry_price=80_000), binance=client, notify=notify,
        )
    finally:
        await client.aclose()
    assert out.action == "alert"
    assert len(notifications) == 1
    assert "liq-near" in notifications[0]


# ---- Auto-close zone: < 10% buffer → close + notify --------------------


@pytest.mark.asyncio
async def test_auto_close_when_buffer_below_10pct() -> None:
    """Original distance $8k. Current $72.5k → distance $0.5k, buffer 6.25%.
    Below 10% → auto-close fires."""
    state = PositionState(
        symbol="BTCUSDT", position_amt=0.05,
        entry_price=72_500,
        leverage=10,
        liquidation_price=72_000,
        unrealized_pnl=-200,
    )
    closed_orders: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/positionRisk" in url:
            return httpx.Response(200, json=[{
                "symbol": "BTCUSDT",
                "positionAmt": str(state.position_amt),
                "entryPrice": str(state.entry_price),
                "leverage": str(state.leverage),
                "liquidationPrice": str(state.liquidation_price),
                "unRealizedProfit": str(state.unrealized_pnl),
            }])
        if "/order" in url:
            closed_orders.append({
                "side": req.url.params.get("side"),
                "reduceOnly": req.url.params.get("reduceOnly"),
            })
            return httpx.Response(200, json={
                "orderId": 999, "symbol": "BTCUSDT", "side": "SELL",
                "executedQty": "0.05", "avgPrice": "72500",
                "status": "FILLED",
            })
        return httpx.Response(404)

    client = BinanceLiveClient(
        api_key="k", api_secret="s",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    notifications: list[str] = []

    async def notify(msg: str) -> None:
        notifications.append(msg)

    try:
        out = await evaluate_position(
            pos=_pos(entry_price=80_000), binance=client, notify=notify,
        )
    finally:
        await client.aclose()
    assert out.action == "auto_closed"
    assert len(closed_orders) == 1
    assert closed_orders[0]["side"] == "SELL"
    assert closed_orders[0]["reduceOnly"] == "true"
    assert any("AUTO-CLOSED" in n for n in notifications)


# ---- Binance API failure ------------------------------------------------


@pytest.mark.asyncio
async def test_binance_error_returns_error_outcome() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"msg": "service unavailable"})
    client = BinanceLiveClient(
        api_key="k", api_secret="s",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        out = await evaluate_position(pos=_pos(), binance=client)
    finally:
        await client.aclose()
    assert out.action == "error"
