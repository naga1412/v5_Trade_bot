"""Integration tests for GET /api/v1/bot-status/promotion-gate."""

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


async def _seed(factory: Any, payloads: list[dict[str, Any]]) -> None:
    async with factory() as session:
        for p in payloads:
            await insert_with_chain(session, "shadow_trades", p)
        await session.commit()


@pytest.mark.asyncio
async def test_promotion_gate_under_threshold(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    # 5 small wins over only ~3 days — fails days, trades, and probably sharpe.
    payloads = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0,
               closed_at=now - timedelta(days=i), signal_id=f"sig{i}")
        for i in range(5)
    ]
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    assert r.status_code == 200
    body = r.json()

    assert body["target_mode"] == "telegram-approve"
    assert len(body["metrics"]) == 6
    assert body["all_passing"] is False
    names = {m["name"] for m in body["metrics"]}
    assert names == {
        "continuous_paper_trading_days", "closed_paper_trades",
        "sharpe_annualized", "max_drawdown",
        "win_rate", "profit_factor",
    }

    days_metric = next(m for m in body["metrics"]
                       if m["name"] == "continuous_paper_trading_days")
    assert days_metric["passing"] is False
    assert days_metric["operator"] == ">="
    assert days_metric["threshold"] == 30

    trades_metric = next(m for m in body["metrics"]
                         if m["name"] == "closed_paper_trades")
    assert trades_metric["passing"] is False
    assert trades_metric["threshold"] == 100

    # Distance summary mentions both days and trades shortfall.
    assert "day" in body["distance_summary"].lower()
    assert "trade" in body["distance_summary"].lower()


@pytest.mark.asyncio
async def test_promotion_gate_at_threshold_passes(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    now = datetime.now(UTC)
    # Generate exactly enough trades over exactly 30 days, alternating
    # winner/loser ratios to satisfy: win_rate >= 0.40, PF >= 1.5,
    # max_dd <= 0.12, sharpe >= 1.0
    # 100 trades, 60 wins (+1.0%), 40 losses (-0.5%), spread across 30 days.
    payloads = []
    # Anchor first trade slightly inside the 30d window to leave headroom for
    # wall-clock drift between test setup and request handling.
    base = now - timedelta(days=29, hours=18)
    for i in range(100):
        # Spread 100 trades across ~29.5 days, last trade ~6h ago.
        offset_hours = (i / 99.0) * (29.5 * 24.0)
        closed_at = base + timedelta(hours=offset_hours)
        # Interleave so cumulative equity climbs steadily with tiny dips.
        # Pattern WLWWL: +1.0, -0.1, +1.0, +1.0, -0.1 per 5-slot cycle -> small DD.
        cycle = i % 5
        is_win = cycle != 1 and cycle != 4
        if is_win:
            payloads.append(_trade(
                symbol="BTC/USDT", direction="LONG",
                entry=100.0, exit_price=101.0, closed_at=closed_at,
                signal_id=f"win{i}"))
        else:
            payloads.append(_trade(
                symbol="BTC/USDT", direction="LONG",
                entry=100.0, exit_price=99.9, closed_at=closed_at,
                signal_id=f"loss{i}"))
    # Insert oldest first so audit chain ordering is sane.
    payloads.sort(key=lambda p: p["closed_at"])
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    assert r.status_code == 200
    body = r.json()
    # Per-metric: enumerate
    for m in body["metrics"]:
        if m["name"] == "closed_paper_trades":
            assert m["current"] == 100
            assert m["passing"] is True
        elif m["name"] == "continuous_paper_trading_days":
            assert m["current"] >= 30
            assert m["passing"] is True
        elif m["name"] == "win_rate":
            assert m["current"] == pytest.approx(0.60)
            assert m["passing"] is True
        elif m["name"] == "profit_factor":
            assert m["passing"] is True
        elif m["name"] == "max_drawdown":
            assert m["passing"] is True
        elif m["name"] == "sharpe_annualized":
            # 100 trades over 30d with stable +1/-0.5 returns -> high sharpe
            assert m["passing"] is True
    assert body["all_passing"] is True


@pytest.mark.asyncio
async def test_promotion_gate_no_trades(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    assert r.status_code == 200
    body = r.json()
    assert body["all_passing"] is False
    trades_metric = next(m for m in body["metrics"]
                         if m["name"] == "closed_paper_trades")
    assert trades_metric["current"] == 0
    days_metric = next(m for m in body["metrics"]
                       if m["name"] == "continuous_paper_trading_days")
    assert days_metric["current"] == 0
