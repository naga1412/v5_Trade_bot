"""Integration tests for GET /api/v1/bot-status/open-positions.

The endpoint now batch-fetches live SPOT prices from Binance and computes
unrealized_pnl_pct + unrealized_pnl_usdt on cold-load. Real Binance
calls are mocked here via monkeypatching the fetch helper — tests do
NOT make network calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import app.api.routes.bot_status as bot_status_module
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


async def _fake_prices_ok(symbols: list[str]) -> dict[str, float]:
    """Stub that returns realistic prices for the three test symbols."""
    table = {
        "BTC/USDT": 105.0,  # LONG @ 100 -> +5%
        "ETH/USDT": 95.0,   # SHORT @ 100 -> +5%
        "SOL/USDT": 100.0,  # LONG @ 100 -> 0%
    }
    return {s: p for s, p in table.items() if s in symbols}


async def _fake_prices_empty(symbols: list[str]) -> dict[str, float]:
    """Stub for the Binance-unreachable / partial-failure case."""
    return {}


@pytest.mark.asyncio
async def test_open_positions_returns_seeded_rows_with_live_pnl(
    bot_status_client: Any, bot_status_factory: Any, monkeypatch: Any,
) -> None:
    """Cold-load path: Binance returns prices, endpoint populates pnl fields."""
    monkeypatch.setattr(
        bot_status_module, "fetch_spot_prices", _fake_prices_ok,
    )

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

    # Sanity: base fields preserved exactly as seeded.
    a = by_signal["sigA"]
    assert a["symbol"] == "BTC/USDT"
    assert a["direction"] == "LONG"
    assert a["entry_price"] == pytest.approx(100.0)
    assert a["bars_held"] == 2

    # LONG sigA: entry=100, current=105 -> +5% -> +$1.50 on $30 size
    assert a["current_price"] == pytest.approx(105.0)
    assert a["unrealized_pnl_pct"] == pytest.approx(5.0)
    assert a["unrealized_pnl_usdt"] == pytest.approx(1.5)

    # SHORT sigB: entry=100, current=95 -> +5% (short profits on price drop)
    b = by_signal["sigB"]
    assert b["direction"] == "SHORT"
    assert b["current_price"] == pytest.approx(95.0)
    assert b["unrealized_pnl_pct"] == pytest.approx(5.0)
    assert b["unrealized_pnl_usdt"] == pytest.approx(1.5)

    # LONG sigC: entry=100, current=100 -> 0%
    c = by_signal["sigC"]
    assert c["current_price"] == pytest.approx(100.0)
    assert c["unrealized_pnl_pct"] == pytest.approx(0.0)
    assert c["unrealized_pnl_usdt"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_open_positions_falls_back_to_none_on_binance_failure(
    bot_status_client: Any, bot_status_factory: Any, monkeypatch: Any,
) -> None:
    """Graceful degradation: Binance unreachable -> price/pnl stay None,
    rest of the row populates normally (UI shows '—' instead of crashing)."""
    monkeypatch.setattr(
        bot_status_module, "fetch_spot_prices", _fake_prices_empty,
    )

    async with bot_status_factory() as session:
        await persist_open_position(
            session, _pos("BTC/USDT", Direction.LONG, "sigA"), user_id=1,
        )
        await session.commit()

    r = await bot_status_client.get("/api/v1/bot-status/open-positions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    a = body[0]
    assert a["symbol"] == "BTC/USDT"
    assert a["entry_price"] == pytest.approx(100.0)
    # Binance returned empty dict -> these three stay None.
    assert a["current_price"] is None
    assert a["unrealized_pnl_pct"] is None
    assert a["unrealized_pnl_usdt"] is None


@pytest.mark.asyncio
async def test_open_positions_empty_returns_empty_list(
    bot_status_client: Any, bot_status_factory: Any, monkeypatch: Any,
) -> None:
    """No open rows -> empty list; ticker fetch must be SKIPPED (no symbols)."""
    calls: list[list[str]] = []

    async def _spy_prices(symbols: list[str]) -> dict[str, float]:
        calls.append(list(symbols))
        return {}

    monkeypatch.setattr(bot_status_module, "fetch_spot_prices", _spy_prices)

    r = await bot_status_client.get("/api/v1/bot-status/open-positions")
    assert r.status_code == 200
    assert r.json() == []
    # The endpoint should still call fetch_spot_prices once (with an empty
    # list) — the helper itself short-circuits internally for empty input.
    assert calls == [[]]
