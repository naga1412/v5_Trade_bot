"""T-UI.3 / PR10.5: /open-positions response includes as_of timestamp."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa

import app.api.routes.bot_status as bot_status_module


_NOW = datetime.now(timezone.utc)


async def _fake_prices_empty(symbols: list[str]) -> dict[str, float]:
    return {}


@pytest.mark.asyncio
async def test_open_positions_includes_as_of(
    bot_status_client: Any, bot_status_factory: Any, monkeypatch: Any,
) -> None:
    """The /open-positions response carries an `as_of` server-clock timestamp
    that the UI uses to render the "As of …" footer on the Open Positions card.
    """
    monkeypatch.setattr(
        bot_status_module, "fetch_spot_prices", _fake_prices_empty,
    )

    async with bot_status_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO shadow_open_positions "
            "(user_id, symbol, timeframe, direction, entry_price, stop_loss, "
            " take_profit, position_size_usdt, entry_score, entry_confidence, "
            " entry_atr, bars_held, opened_at, last_check_at, signal_id) "
            "VALUES (1, 'BTCUSDT', '1h', 'LONG', 100.0, 95.0, 110.0, 1000.0, "
            " 0.5, 0.7, 2.0, 0, :now, :now, 'sig-1')"
        ), {"now": _NOW.isoformat()})
        await s.commit()

    before = datetime.now(timezone.utc)
    r = await bot_status_client.get("/api/v1/bot-status/open-positions")
    after = datetime.now(timezone.utc)

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    as_of_raw = body[0]["as_of"]
    as_of = datetime.fromisoformat(as_of_raw)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=2) <= as_of <= after + timedelta(seconds=2)
