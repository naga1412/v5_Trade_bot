"""T-UI.1: shadow_worker emits pnl_tick on non-closing candles."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.shadow.engine import Direction, ShadowPosition


_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_non_closing_candle_emits_pnl_tick() -> None:
    """When check_exit returns None, _maybe_close_position must publish
    a pnl_tick so the dashboard updates."""
    from app.shadow.worker import ShadowWorker

    pos = ShadowPosition(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        position_size_usdt=1000.0,
        entry_score=0.5,
        entry_confidence=0.7,
        entry_atr=2.0,
        layer_scores={},
        bars_held=0,
        opened_at=_NOW,
        last_check_at=_NOW,
        signal_id="test-sig-1",
        timeframe="1h",
    )

    class _Candle:
        symbol = "BTCUSDT"
        ts = _NOW
        high = 102.0
        low = 99.5
        close = 101.0

    worker = ShadowWorker.__new__(ShadowWorker)
    worker.open_positions = {("BTCUSDT", "1h"): pos}

    with patch("app.shadow.worker.shadow_updates.publish_pnl_tick",
               new=AsyncMock(return_value=None)) as pub, \
         patch("app.shadow.worker.check_exit", return_value=None):
        await worker._maybe_close_position(_Candle(), "1h")

    pub.assert_awaited_once()
    _args, kwargs = pub.call_args
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["current_price"] == 101.0
    # LONG @ 100 → 101 = +1%
    assert abs(kwargs["unrealized_pnl_pct"] - 1.0) < 1e-9
