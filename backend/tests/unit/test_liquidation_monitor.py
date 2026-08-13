"""SP-8 Phase J — liquidation monitor tests."""
from __future__ import annotations


import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.exchanges.binance_live import (
    BinanceLiveClient,
    PositionState,
)
from app.trading.execution.liquidation_monitor import (
    OpenPosition,
    _list_open_positions,
    _write_liquidation_buffer_close,
    evaluate_position,
)


def _pos(**overrides) -> OpenPosition:
    base = dict(
        trade_id=42, user_id=1,
        symbol="BTC/USDT", direction="LONG",
        entry_price=80_000.0, stop_loss_price=78_400.0,
        position_value_usdt=800_000.0,
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
    # TIER 1 (defect sweep 2026-08-06): the mark price used for the
    # buffer calc must be threaded out so the DB write can compute pnl.
    assert out.exit_price == pytest.approx(72_500.0)


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


# ---- Regression: May-bug legacy rows must NOT be polled -----------------


@pytest.mark.asyncio
async def test_list_open_positions_skips_may_bug_legacy_rows() -> None:
    """Rows shaped like id=7 (WLD/USDT) / id=9 (BTC/USDT) on 2026-07-22
    have status='closed' AND closed_at NULL AND exit_reason NULL. The old
    `closed_at IS NULL` predicate would sweep them into the monitor and
    thrash on stale entry_price. `status='open'` skips them cleanly.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "entry_price REAL, stop_loss REAL, mtf_agreement INTEGER, "
            "position_value_usdt REAL, "
            "closed_at TEXT, status TEXT, exit_reason TEXT)"
        ))
        # Two legacy rows (May-bug shape) + one real open position.
        await conn.execute(sa.text(
            "INSERT INTO live_trades "
            "(user_id, symbol, direction, entry_price, stop_loss, "
            " position_value_usdt, closed_at, status, exit_reason) "
            "VALUES (1, 'BTC/USDT', 'LONG', 75881.8, 74000, 200.0, NULL, 'closed', NULL), "
            "       (1, 'WLD/USDT', 'LONG', 0.39, 0.36, 250.0, NULL, 'closed', NULL), "
            "       (1, 'ETH/USDT', 'LONG', 3500, 3400, 700.0, NULL, 'open', NULL)"
        ))
    async with AsyncSession(engine) as s:
        positions = await _list_open_positions(s)
    assert len(positions) == 1, (
        f"expected 1 real open position, got {len(positions)}: "
        f"{[p.symbol for p in positions]}"
    )
    assert positions[0].symbol == "ETH/USDT"


# ---- TIER 1 (defect sweep 2026-08-06): pnl_usdt/pnl_pct write path ------


from datetime import datetime, timezone  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.mark.asyncio
async def test_write_liquidation_buffer_close_writes_pnl() -> None:
    """A liquidation-buffer auto-close must also land with non-null
    pnl_usdt AND pnl_pct — this is the second of the two live_trades close
    paths that never wrote them (the other is live_exit_monitor)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE live_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, symbol TEXT, direction TEXT, "
            "entry_price REAL, stop_loss REAL, position_value_usdt REAL, "
            "mtf_agreement INTEGER, exit_reason TEXT, exit_price REAL, "
            "pnl_usdt REAL, pnl_pct REAL, closed_at TEXT, status TEXT)"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE live_cooldowns ("
            "user_id INTEGER NOT NULL, symbol TEXT NOT NULL, "
            "cooldown_until TEXT NOT NULL, last_exit_reason TEXT NOT NULL, "
            "last_mtf_agreement INTEGER, updated_at TEXT NOT NULL, "
            "PRIMARY KEY (user_id, symbol))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO live_trades "
            "(id, user_id, symbol, direction, entry_price, stop_loss, "
            " position_value_usdt, mtf_agreement, status) "
            "VALUES (1, 1, 'BTC/USDT', 'LONG', 80000.0, 78400.0, "
            "        800000.0, 5, 'open')"
        ))

    pos = _pos(trade_id=1, entry_price=80_000.0, position_value_usdt=800_000.0)

    async with AsyncSession(engine) as session:
        await _write_liquidation_buffer_close(
            session, pos=pos, exit_price=72_500.0,
            settings=get_settings(), now=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )
        await session.commit()

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT pnl_usdt, pnl_pct, exit_price FROM live_trades WHERE id = 1"
        ))).first()
        assert row.exit_price == pytest.approx(72_500.0)
        assert row.pnl_usdt is not None
        assert row.pnl_pct is not None
        # LONG, entry=80000 -> exit=72500, position_value=800000: -9.375%
        assert row.pnl_pct == pytest.approx(-9.375)
        assert row.pnl_usdt == pytest.approx(-75_000.0)
