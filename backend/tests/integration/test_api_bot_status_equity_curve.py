"""Integration tests for GET /api/v1/bot-status/equity-curve."""

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
async def test_equity_curve_groups_by_day_and_accumulates(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    # Anchor relative to "now" so the test stays within the 30-day window
    # at any wall-clock time CI runs. We pick a midnight ~10 days ago so
    # day-bucketing is deterministic but always inside ?days=30.
    today_midnight = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    base = today_midnight - timedelta(days=10)
    rows = [
        # Day 1 — one win
        _trade(direction="LONG", entry=100.0, exit_price=110.0,
               closed_at=base + timedelta(days=0, hours=10),
               signal_id="d1a"),
        # Day 3 — one win + one loss (net positive)
        _trade(direction="LONG", entry=100.0, exit_price=110.0,
               closed_at=base + timedelta(days=2, hours=8),
               signal_id="d3a"),
        _trade(direction="LONG", entry=100.0, exit_price=95.0,
               closed_at=base + timedelta(days=2, hours=20),
               signal_id="d3b"),
        # Day 7 — one loss
        _trade(direction="SHORT", entry=100.0, exit_price=105.0,
               closed_at=base + timedelta(days=6, hours=5),
               signal_id="d7a"),
    ]
    async with bot_status_factory() as session:
        for r in rows:
            await insert_with_chain(session, "shadow_trades", r)
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/equity-curve?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 30
    pts = body["points"]
    # Only days with trades produce points -> 3 buckets.
    assert len(pts) == 3
    # Day 1 cumulative = +3.0 USDT
    assert pts[0]["cumulative_pnl_usdt"] == pytest.approx(3.0)
    # Day 3 cumulative = +3 + 3 - 1.5 = +4.5 USDT
    assert pts[1]["cumulative_pnl_usdt"] == pytest.approx(4.5)
    # Day 7 cumulative = +4.5 - 1.5 = +3.0 USDT
    assert pts[2]["cumulative_pnl_usdt"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_equity_curve_empty_window(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/equity-curve")
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 30
    assert body["points"] == []


@pytest.mark.asyncio
async def test_equity_curve_validates_days(bot_status_client: Any) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/equity-curve?days=0")
    assert r.status_code == 422
    r = await bot_status_client.get("/api/v1/bot-status/equity-curve?days=400")
    assert r.status_code == 422
