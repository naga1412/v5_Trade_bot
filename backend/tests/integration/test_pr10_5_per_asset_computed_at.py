"""T-UI.3 / PR10.5: /per-asset response includes computed_at + last_trade_closed_at."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import sqlalchemy as sa


_NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_per_asset_timestamps(
    bot_status_client: Any, bot_status_factory: Any,
) -> None:
    """The /per-asset response includes `computed_at` (server clock at
    response time) and `last_trade_closed_at` (max closed_at across the
    symbol's window). The UI uses these for the "Computed at … · Last
    trade: …" footer on the Per-Asset Stats card.
    """
    last_closed = _NOW - timedelta(hours=2)
    async with bot_status_factory() as s:
        await s.execute(sa.text(
            "INSERT INTO shadow_trades "
            "(user_id, symbol, timeframe, direction, entry_price, stop_loss, "
            " take_profit, position_size_usdt, entry_score, entry_confidence, "
            " layer_scores, entry_atr, opened_at, closed_at, pnl_pct, "
            " pnl_usdt, bars_held, exit_reason, inputs_hash, model_version, "
            " signal_id, prev_hash, row_hash) "
            "VALUES (1, 'BTCUSDT', '1h', 'LONG', 100, 95, 110, 1000, "
            " 0.5, 0.7, '{}', 2.0, :opened, :closed, "
            " 5.0, 50.0, 5, 'take_profit', 'hsh', 'sp-0', "
            " 'sig-x', '0', 'h-x')"
        ), {"opened": (_NOW - timedelta(hours=3)).isoformat(),
            "closed": last_closed.isoformat()})
        await s.commit()

    before = datetime.now(timezone.utc)
    r = await bot_status_client.get("/api/v1/bot-status/per-asset?days=30")
    after = datetime.now(timezone.utc)

    assert r.status_code == 200
    body = r.json()
    btc = next(b for b in body if b["symbol"] == "BTCUSDT")

    computed_at = datetime.fromisoformat(btc["computed_at"])
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=2) <= computed_at <= after + timedelta(seconds=2)

    last = datetime.fromisoformat(btc["last_trade_closed_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    assert abs((last - last_closed).total_seconds()) < 2
