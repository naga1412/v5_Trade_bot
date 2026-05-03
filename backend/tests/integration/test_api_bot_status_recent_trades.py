"""Integration tests for GET /api/v1/bot-status/recent-trades."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.audit import insert_with_chain


def _trade(
    *, symbol: str, direction: str, entry: float, exit_price: float,
    closed_at: datetime, signal_id: str, exit_reason: str | None = None,
    position_size_usdt: float = 30.0,
) -> dict[str, Any]:
    sl = entry * 0.95 if direction == "LONG" else entry * 1.05
    tp = entry * 1.10 if direction == "LONG" else entry * 0.90
    if direction == "LONG":
        pnl_pct = (exit_price - entry) / entry * 100.0
    else:
        pnl_pct = (entry - exit_price) / entry * 100.0
    pnl_usdt = position_size_usdt * pnl_pct / 100.0
    if exit_reason is None:
        exit_reason = "TAKE_PROFIT" if pnl_pct > 0 else "STOP_LOSS"
    opened_at = closed_at - timedelta(hours=4)
    return {
        "symbol": symbol, "timeframe": "1h", "direction": direction,
        "entry_price": entry, "stop_loss": sl, "take_profit": tp,
        "position_size_usdt": position_size_usdt,
        "entry_score": 0.6, "entry_confidence": 0.7,
        "layer_scores": json.dumps({}), "entry_atr": 1.0,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct, "pnl_usdt": pnl_usdt, "bars_held": 4,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "inputs_hash": "h" * 64, "model_version": "sp-0.5",
        "signal_id": signal_id,
    }


async def _seed_mixed(factory: Any) -> datetime:
    now = datetime.now(UTC)
    rows = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0,
               closed_at=now - timedelta(hours=1), signal_id="r1"),
        _trade(symbol="BTC/USDT", direction="SHORT",
               entry=100.0, exit_price=105.0,
               closed_at=now - timedelta(hours=2), signal_id="r2"),
        _trade(symbol="ETH/USDT", direction="LONG",
               entry=50.0, exit_price=55.0,
               closed_at=now - timedelta(hours=3), signal_id="r3"),
        _trade(symbol="ETH/USDT", direction="SHORT",
               entry=50.0, exit_price=45.0,
               closed_at=now - timedelta(hours=4), signal_id="r4"),
        _trade(symbol="SOL/USDT", direction="LONG",
               entry=20.0, exit_price=18.0,
               closed_at=now - timedelta(hours=5), signal_id="r5",
               exit_reason="TIMEOUT"),
    ]
    async with factory() as session:
        for r in rows:
            await insert_with_chain(session, "shadow_trades", r)
        await session.commit()
    return now


@pytest.mark.asyncio
async def test_recent_trades_default_returns_all_sorted_desc(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get("/api/v1/bot-status/recent-trades")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 5
    # Sorted by closed_at desc -> r1 first
    assert [t["signal_id"] for t in body] == ["r1", "r2", "r3", "r4", "r5"]
    # Spot-check fields
    assert body[0]["symbol"] == "BTC/USDT"
    assert body[0]["direction"] == "LONG"
    assert body[0]["exit_reason"] == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_recent_trades_filter_by_symbol_url_safe(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get(
        "/api/v1/bot-status/recent-trades?symbol=BTC-USDT"
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert {t["symbol"] for t in body} == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_recent_trades_filter_by_direction(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get(
        "/api/v1/bot-status/recent-trades?direction=SHORT"
    )
    assert r.status_code == 200
    body = r.json()
    assert all(t["direction"] == "SHORT" for t in body)
    assert len(body) == 2


@pytest.mark.asyncio
async def test_recent_trades_filter_by_result_win(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get(
        "/api/v1/bot-status/recent-trades?result=win"
    )
    assert r.status_code == 200
    body = r.json()
    assert all(t["pnl_pct"] > 0 for t in body)
    # r1, r3, r4 are wins -> 3 entries
    assert {t["signal_id"] for t in body} == {"r1", "r3", "r4"}


@pytest.mark.asyncio
async def test_recent_trades_filter_by_result_loss(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get(
        "/api/v1/bot-status/recent-trades?result=loss"
    )
    assert r.status_code == 200
    body = r.json()
    assert all(t["pnl_pct"] <= 0 for t in body)
    assert {t["signal_id"] for t in body} == {"r2", "r5"}


@pytest.mark.asyncio
async def test_recent_trades_combined_filters(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get(
        "/api/v1/bot-status/recent-trades"
        "?symbol=ETH/USDT&direction=LONG&result=win"
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["signal_id"] == "r3"


@pytest.mark.asyncio
async def test_recent_trades_limit_param(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    await _seed_mixed(bot_status_factory)
    r = await bot_status_client.get("/api/v1/bot-status/recent-trades?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_recent_trades_limit_validation(bot_status_client: Any) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/recent-trades?limit=0")
    assert r.status_code == 422
    r = await bot_status_client.get("/api/v1/bot-status/recent-trades?limit=600")
    assert r.status_code == 422
