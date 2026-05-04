"""Integration tests for GET /api/v1/bot-status/long-vs-short."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.audit import insert_with_chain


def _trade(
    *, direction: str, entry: float, exit_price: float,
    closed_at: datetime, signal_id: str,
    position_size_usdt: float = 30.0,
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
        "symbol": "BTC/USDT", "timeframe": "1h", "direction": direction,
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
async def test_long_vs_short_correct_breakdown(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    # 5 LONGs (3 wins, 2 losses)
    for i, win in enumerate([True, True, True, False, False]):
        rows.append(_trade(
            direction="LONG",
            entry=100.0, exit_price=110.0 if win else 95.0,
            closed_at=now - timedelta(days=i + 1),
            signal_id=f"long{i}",
        ))
    # 5 SHORTs (2 wins, 3 losses)
    for i, win in enumerate([True, True, False, False, False]):
        rows.append(_trade(
            direction="SHORT",
            entry=100.0, exit_price=90.0 if win else 105.0,
            closed_at=now - timedelta(days=i + 6),
            signal_id=f"short{i}",
        ))
    async with bot_status_factory() as session:
        for r in rows:
            await insert_with_chain(session, "shadow_trades", r)
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/long-vs-short?days=30")
    assert r.status_code == 200
    body = r.json()
    assert "long" in body and "short" in body
    assert body["long"]["trades"] == 5
    assert body["long"]["win_rate"] == pytest.approx(0.60)
    assert body["short"]["trades"] == 5
    assert body["short"]["win_rate"] == pytest.approx(0.40)
    # Long is profitable: 3*10 - 2*5 = 20 USDT * 0.3 = 6 USDT (well, $30*10%=$3)
    # 3 wins at +10% on $30 = $9; 2 losses at -5% on $30 = -$3; net +$6
    assert body["long"]["pnl_usdt"] == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_long_vs_short_default_days(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/long-vs-short")
    assert r.status_code == 200
    body = r.json()
    assert body["long"]["window"] == "30d"
    assert body["short"]["window"] == "30d"


@pytest.mark.asyncio
async def test_long_vs_short_validates_days(bot_status_client: Any) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/long-vs-short?days=0")
    assert r.status_code == 422
    r = await bot_status_client.get("/api/v1/bot-status/long-vs-short?days=400")
    assert r.status_code == 422
