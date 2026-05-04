"""Integration tests for GET /api/v1/bot-status/per-asset."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.audit import insert_with_chain


def _trade(
    *, symbol: str, direction: str, entry: float, exit_price: float,
    closed_at: datetime, signal_id: str, position_size_usdt: float = 30.0,
) -> dict[str, Any]:
    sl = entry * 0.95 if direction == "LONG" else entry * 1.05
    tp = entry * 1.10 if direction == "LONG" else entry * 0.90
    if direction == "LONG":
        pnl_pct = (exit_price - entry) / entry * 100.0
    else:
        pnl_pct = (entry - exit_price) / entry * 100.0
    pnl_usdt = position_size_usdt * pnl_pct / 100.0
    opened_at = closed_at - timedelta(hours=4)
    return {
        "symbol": symbol, "timeframe": "1h", "direction": direction,
        "entry_price": entry, "stop_loss": sl, "take_profit": tp,
        "position_size_usdt": position_size_usdt,
        "entry_score": 0.6, "entry_confidence": 0.7,
        "layer_scores": json.dumps({}), "entry_atr": 1.0,
        "exit_price": exit_price,
        "exit_reason": "TAKE_PROFIT" if pnl_pct > 0 else "STOP_LOSS",
        "pnl_pct": pnl_pct, "pnl_usdt": pnl_usdt, "bars_held": 4,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "h" * 64, "model_version": "sp-0.5",
        "signal_id": signal_id,
    }


@pytest.mark.asyncio
async def test_per_asset_groups_and_sorts(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    rows = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0,
               closed_at=now - timedelta(days=1), signal_id="b1"),
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=105.0,
               closed_at=now - timedelta(days=2), signal_id="b2"),
        _trade(symbol="ETH/USDT", direction="SHORT",
               entry=50.0, exit_price=45.0,
               closed_at=now - timedelta(days=3), signal_id="e1"),
        _trade(symbol="SOL/USDT", direction="LONG",
               entry=20.0, exit_price=19.0,  # loss
               closed_at=now - timedelta(days=4), signal_id="s1"),
    ]
    async with bot_status_factory() as session:
        for r in rows:
            await insert_with_chain(session, "shadow_trades", r)
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/per-asset?days=30")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    by_sym = {e["symbol"]: e for e in body}
    assert by_sym["BTC/USDT"]["trades"] == 2
    assert by_sym["BTC/USDT"]["win_rate"] == pytest.approx(1.0)
    assert by_sym["ETH/USDT"]["trades"] == 1
    assert by_sym["SOL/USDT"]["trades"] == 1
    # SOL is a loser; pnl_usdt < 0
    assert by_sym["SOL/USDT"]["pnl_usdt"] < 0

    # Sorted by pnl_usdt descending
    pnls = [e["pnl_usdt"] for e in body]
    assert pnls == sorted(pnls, reverse=True)


@pytest.mark.asyncio
async def test_per_asset_default_days_is_30(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    # One trade inside window, one outside.
    inside = _trade(symbol="BTC/USDT", direction="LONG",
                    entry=100.0, exit_price=110.0,
                    closed_at=now - timedelta(days=10), signal_id="x1")
    outside = _trade(symbol="BTC/USDT", direction="LONG",
                     entry=100.0, exit_price=110.0,
                     closed_at=now - timedelta(days=45), signal_id="x2")
    async with bot_status_factory() as session:
        await insert_with_chain(session, "shadow_trades", inside)
        await insert_with_chain(session, "shadow_trades", outside)
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/per-asset")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["trades"] == 1


@pytest.mark.asyncio
async def test_per_asset_validates_days_range(
    bot_status_client: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/per-asset?days=0")
    assert r.status_code == 422
    r = await bot_status_client.get("/api/v1/bot-status/per-asset?days=400")
    assert r.status_code == 422
