"""Integration tests for GET /api/v1/bot-status/open-positions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.shadow.engine import Direction, ShadowPosition
from app.shadow.persistence import persist_open_position


def _pos(symbol: str, direction: Direction, signal_id: str) -> ShadowPosition:
    opened_at = datetime(2026, 5, 1, 10, tzinfo=UTC)
    return ShadowPosition(
        symbol=symbol, direction=direction,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size_usdt=30.0,
        entry_score=0.7, entry_confidence=0.8,
        entry_atr=2.0, layer_scores={}, bars_held=2,
        opened_at=opened_at,
        last_check_at=opened_at + timedelta(hours=2),
        signal_id=signal_id,
    )


@pytest.mark.asyncio
async def test_open_positions_returns_seeded_rows(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    async with bot_status_factory() as session:
        await persist_open_position(
            session, _pos("BTC/USDT", Direction.LONG, "sigA"), user_id=1,
        )
        await persist_open_position(
            session, _pos("ETH/USDT", Direction.SHORT, "sigB"), user_id=1,
        )
        await persist_open_position(
            session, _pos("SOL/USDT", Direction.LONG, "sigC"), user_id=1,
        )
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/open-positions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    by_signal = {p["signal_id"]: p for p in body}
    assert set(by_signal) == {"sigA", "sigB", "sigC"}
    a = by_signal["sigA"]
    assert a["symbol"] == "BTC/USDT"
    assert a["direction"] == "LONG"
    assert a["entry_price"] == pytest.approx(100.0)
    assert a["stop_loss"] == pytest.approx(95.0)
    assert a["take_profit"] == pytest.approx(110.0)
    assert a["position_size_usdt"] == pytest.approx(30.0)
    assert a["bars_held"] == 2
    # Per docstring contract: REST does NOT fetch live mark price.
    assert a["current_price"] is None
    assert a["unrealized_pnl_pct"] is None
    assert a["unrealized_pnl_usdt"] is None


@pytest.mark.asyncio
async def test_open_positions_empty_returns_empty_list(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    r = await bot_status_client.get("/api/v1/bot-status/open-positions")
    assert r.status_code == 200
    assert r.json() == []
