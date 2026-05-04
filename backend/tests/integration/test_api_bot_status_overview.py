"""Integration tests for GET /api/v1/bot-status/overview."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from app.db.audit import insert_with_chain


def _trade_payload(
    *, symbol: str, direction: str, entry: float, sl: float, tp: float,
    exit_price: float, exit_reason: str, closed_at: datetime,
    position_size_usdt: float = 30.0, signal_id: str | None = None,
    bars_held: int = 4,
) -> dict[str, Any]:
    if direction == "LONG":
        pnl_pct = (exit_price - entry) / entry * 100.0
    else:
        pnl_pct = (entry - exit_price) / entry * 100.0
    pnl_usdt = position_size_usdt * pnl_pct / 100.0
    opened_at = closed_at - timedelta(hours=bars_held)
    return {
        "symbol": symbol,
        "timeframe": "1h",
        "direction": direction,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "position_size_usdt": position_size_usdt,
        "entry_score": 0.6,
        "entry_confidence": 0.7,
        "layer_scores": json.dumps({}),
        "entry_atr": 1.0,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct,
        "pnl_usdt": pnl_usdt,
        "bars_held": bars_held,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "hash" * 16,
        "model_version": "sp-0.5",
        "signal_id": signal_id or f"sig{id(closed_at) % 100000}{symbol}{direction}",
    }


async def _seed_overview_trades(factory: Any, *, now: datetime) -> None:
    """Seed ~10 trades spanning 30d, mix of LONG/SHORT, mix of TP/SL."""
    rows = [
        # LAST 24H — 2 trades (1 LONG win, 1 SHORT win)
        _trade_payload(symbol="BTC/USDT", direction="LONG",
                       entry=100.0, sl=95.0, tp=110.0, exit_price=110.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(hours=2),
                       signal_id="sig24h-1"),
        _trade_payload(symbol="ETH/USDT", direction="SHORT",
                       entry=50.0, sl=55.0, tp=45.0, exit_price=45.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(hours=10),
                       signal_id="sig24h-2"),
        # LAST 7D — 3 more trades (1 SL loss LONG, 1 SL loss SHORT, 1 TP LONG)
        _trade_payload(symbol="BTC/USDT", direction="LONG",
                       entry=100.0, sl=95.0, tp=110.0, exit_price=95.0,
                       exit_reason="STOP_LOSS",
                       closed_at=now - timedelta(days=2),
                       signal_id="sig7d-1"),
        _trade_payload(symbol="BTC/USDT", direction="SHORT",
                       entry=100.0, sl=105.0, tp=90.0, exit_price=105.0,
                       exit_reason="STOP_LOSS",
                       closed_at=now - timedelta(days=4),
                       signal_id="sig7d-2"),
        _trade_payload(symbol="ETH/USDT", direction="LONG",
                       entry=50.0, sl=47.5, tp=55.0, exit_price=55.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(days=6),
                       signal_id="sig7d-3"),
        # LAST 30D — 5 more trades
        _trade_payload(symbol="ETH/USDT", direction="SHORT",
                       entry=50.0, sl=52.5, tp=45.0, exit_price=45.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(days=10),
                       signal_id="sig30d-1"),
        _trade_payload(symbol="BTC/USDT", direction="LONG",
                       entry=100.0, sl=95.0, tp=110.0, exit_price=110.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(days=15),
                       signal_id="sig30d-2"),
        _trade_payload(symbol="BTC/USDT", direction="SHORT",
                       entry=100.0, sl=105.0, tp=90.0, exit_price=90.0,
                       exit_reason="TAKE_PROFIT",
                       closed_at=now - timedelta(days=20),
                       signal_id="sig30d-3"),
        _trade_payload(symbol="ETH/USDT", direction="SHORT",
                       entry=50.0, sl=52.5, tp=45.0, exit_price=52.5,
                       exit_reason="STOP_LOSS",
                       closed_at=now - timedelta(days=25),
                       signal_id="sig30d-4"),
        _trade_payload(symbol="ETH/USDT", direction="LONG",
                       entry=50.0, sl=47.5, tp=55.0, exit_price=47.5,
                       exit_reason="STOP_LOSS",
                       closed_at=now - timedelta(days=29),
                       signal_id="sig30d-5"),
    ]
    async with factory() as session:
        for r in rows:
            await insert_with_chain(session, "shadow_trades", r)
        await session.commit()


@pytest.mark.asyncio
async def test_overview_returns_five_window_blocks(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    await _seed_overview_trades(bot_status_factory, now=now)

    r = await bot_status_client.get("/api/v1/bot-status/overview")
    assert r.status_code == 200, r.text
    body = r.json()

    for key in ("last_24h", "last_7d", "last_30d", "long_only_30d", "short_only_30d"):
        assert key in body, f"missing window {key}"
        block = body[key]
        for f in ("trades", "pnl_usdt", "pnl_pct", "win_rate",
                  "sharpe_annualized", "max_drawdown", "profit_factor"):
            assert f in block, f"{key} missing field {f}"

    # 24h block has exactly 2 trades, both winners
    assert body["last_24h"]["trades"] == 2
    assert body["last_24h"]["win_rate"] == pytest.approx(1.0)
    # 7d block has 24h trades + 3 more = 5
    assert body["last_7d"]["trades"] == 5
    # 30d has all 10
    assert body["last_30d"]["trades"] == 10
    # long-only-30d: 5 (rows tagged LONG above)
    assert body["long_only_30d"]["trades"] == 5
    # short-only-30d: 5
    assert body["short_only_30d"]["trades"] == 5
    # window labels
    assert body["last_24h"]["window"] == "24h"
    assert body["last_7d"]["window"] == "7d"
    assert body["last_30d"]["window"] == "30d"


@pytest.mark.asyncio
async def test_overview_empty_db(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["last_24h"]["trades"] == 0
    assert body["last_24h"]["pnl_usdt"] == pytest.approx(0.0)
    assert body["last_24h"]["pnl_pct"] is None
    assert body["last_24h"]["sharpe_annualized"] is None


@pytest.mark.asyncio
async def test_overview_does_not_leak_other_users_trades(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """Seed user 2 with a closed trade; user 1's overview must show zero."""
    async with bot_status_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO users (id, email, display_name, is_admin) "
            "VALUES (2, 'other@local', 'Other', 0)"
        ))
        await session.commit()

    now = datetime.now(UTC)
    payload = {
        **_trade_payload(
            symbol="BTC/USDT", direction="LONG",
            entry=100.0, sl=95.0, tp=110.0, exit_price=110.0,
            exit_reason="TAKE_PROFIT",
            closed_at=now - timedelta(hours=1),
            signal_id="other-user-1",
        ),
        "user_id": 2,
    }
    async with bot_status_factory() as session:
        await insert_with_chain(session, "shadow_trades", payload)
        await session.commit()

    # bot_status_client is bound to user_id=1 — the other user's trade must NOT leak.
    r = await bot_status_client.get("/api/v1/bot-status/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_24h"]["trades"] == 0
    assert body["last_30d"]["trades"] == 0
