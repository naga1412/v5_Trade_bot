"""PR3 Phase 7: /promotion-gate per_timeframe breakdown.

Spec §4.8: response carries a `per_timeframe` block alongside the combined
metrics. The combined block excludes 15m trades when
`SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False` so the promotion criterion is
not gambled on the noisier TF until staging validates its win-rate.

Test matrix:
  - default (15m NOT eligible) → combined excludes 15m; per_timeframe
    shows both TFs separately
  - operator flips SHADOW_15M_ELIGIBLE_FOR_PROMOTION=True → combined
    includes 15m
  - per_timeframe block is informational only (does NOT change
    all_passing). Test asserts per-TF metrics exist whether or not the
    flag is flipped.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.db.audit import insert_with_chain


def _trade(
    *, symbol: str, direction: str, entry: float, exit_price: float,
    closed_at: datetime, signal_id: str, timeframe: str = "1h",
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
        "symbol": symbol, "timeframe": timeframe, "direction": direction,
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
async def test_per_timeframe_block_present_with_both_tfs(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """Response includes `per_timeframe` keyed by SHADOW_TIMEFRAMES (default
    ['1h', '15m']). Empty TFs surface as zero-trade blocks (operator can
    watch 15m accrue toward future eligibility)."""
    now = datetime.now(UTC)
    payloads = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0, timeframe="1h",
               closed_at=now - timedelta(days=i), signal_id=f"sig1h_{i}")
        for i in range(3)
    ] + [
        _trade(symbol="ETH/USDT", direction="LONG",
               entry=2000.0, exit_price=2200.0, timeframe="15m",
               closed_at=now - timedelta(hours=i * 6), signal_id=f"sig15m_{i}")
        for i in range(2)
    ]
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    assert r.status_code == 200
    body = r.json()
    assert "per_timeframe" in body
    per_tf = body["per_timeframe"]
    assert "1h" in per_tf
    assert "15m" in per_tf
    assert per_tf["1h"]["trades_total"] == 3
    assert per_tf["15m"]["trades_total"] == 2


@pytest.mark.asyncio
async def test_combined_excludes_15m_when_not_eligible_for_promotion(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """Default SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False → combined trade
    count excludes 15m rows. per_timeframe shows them separately."""
    now = datetime.now(UTC)
    payloads = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0, timeframe="1h",
               closed_at=now - timedelta(days=i), signal_id=f"sig1h_{i}")
        for i in range(5)
    ] + [
        _trade(symbol="ETH/USDT", direction="LONG",
               entry=2000.0, exit_price=2200.0, timeframe="15m",
               closed_at=now - timedelta(hours=i * 4), signal_id=f"sig15m_{i}")
        for i in range(7)
    ]
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    body = r.json()

    closed_trades_metric = next(
        m for m in body["metrics"] if m["name"] == "closed_paper_trades"
    )
    # Combined excludes 15m → only the 5 1h trades count
    assert closed_trades_metric["current"] == 5.0
    # per_timeframe still surfaces all of them
    assert body["per_timeframe"]["1h"]["trades_total"] == 5
    assert body["per_timeframe"]["15m"]["trades_total"] == 7


@pytest.mark.asyncio
async def test_combined_includes_15m_when_eligible(
    bot_status_client: Any, bot_status_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator flips SHADOW_15M_ELIGIBLE_FOR_PROMOTION=True → combined
    block includes 15m. per_timeframe is unchanged (still informational)."""
    monkeypatch.setenv("SHADOW_15M_ELIGIBLE_FOR_PROMOTION", "true")
    # Bust the lru_cache so the new env var is picked up.
    from app.config import get_settings
    get_settings.cache_clear()

    now = datetime.now(UTC)
    payloads = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0, timeframe="1h",
               closed_at=now - timedelta(days=i), signal_id=f"sig1h_{i}")
        for i in range(5)
    ] + [
        _trade(symbol="ETH/USDT", direction="LONG",
               entry=2000.0, exit_price=2200.0, timeframe="15m",
               closed_at=now - timedelta(hours=i * 4), signal_id=f"sig15m_{i}")
        for i in range(7)
    ]
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    body = r.json()

    closed_trades_metric = next(
        m for m in body["metrics"] if m["name"] == "closed_paper_trades"
    )
    # Combined NOW includes 15m → 5 + 7 = 12
    assert closed_trades_metric["current"] == 12.0
    assert body["per_timeframe"]["1h"]["trades_total"] == 5
    assert body["per_timeframe"]["15m"]["trades_total"] == 7

    # Restore default for other tests
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_per_timeframe_empty_tf_yields_zero_block(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """A configured TF with no trades surfaces as trades_total=0 +
    None metrics. Lets operator see "15m lane configured but quiet"
    instead of the TF disappearing entirely from the response."""
    now = datetime.now(UTC)
    payloads = [
        _trade(symbol="BTC/USDT", direction="LONG",
               entry=100.0, exit_price=110.0, timeframe="1h",
               closed_at=now - timedelta(days=i), signal_id=f"sig1h_{i}")
        for i in range(2)
    ]  # NO 15m trades at all
    await _seed(bot_status_factory, payloads)

    r = await bot_status_client.get("/api/v1/bot-status/promotion-gate")
    body = r.json()
    assert body["per_timeframe"]["15m"]["trades_total"] == 0
    assert body["per_timeframe"]["15m"]["sharpe"] is None
    assert body["per_timeframe"]["15m"]["win_rate"] is None
