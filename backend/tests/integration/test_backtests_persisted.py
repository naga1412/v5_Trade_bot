"""Integration test: BacktestResult persists to the backtests table (SP-7 Phase B4)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from tools.backtest import BacktestResult, persist_backtest_result


@pytest.mark.asyncio
async def test_persist_backtest_result_writes_row(bot_status_factory) -> None:
    """A BacktestResult round-trips into the backtests row + reads back identically."""
    session_factory = bot_status_factory  # uses the SP-0.5 in-memory SQLite engine
    async with session_factory() as session:
        result = BacktestResult(
            n_trades=2, win_rate=0.5, profit_factor=1.0, sharpe=1.2,
            max_drawdown=0.05,
            equity_curve=[(datetime(2025, 1, 1, tzinfo=timezone.utc), 10000.0)],
            trade_log=[],
            params_hash="testhash123",
            initial_balance=10000.0, final_balance=10100.0,
            symbol="BTC/USDT", timeframe="1h",
            start_ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_ts=datetime(2025, 2, 1, tzinfo=timezone.utc),
            layer_weights={i: 1 / 9 for i in range(1, 10)},
        )
        new_id = await persist_backtest_result(
            session, result=result, triggered_by_user_id=1,
        )
        await session.commit()

        row = (await session.execute(
            sa.text("SELECT * FROM backtests WHERE id = :i"), {"i": new_id}
        )).first()
    assert row is not None
    assert row.symbol == "BTC/USDT"
    assert row.params_hash == "testhash123"
    assert row.n_trades == 2
    assert row.timeframe == "1h"
    assert row.status == "completed"
    weights_loaded = (
        json.loads(row.layer_weights)
        if isinstance(row.layer_weights, str)
        else row.layer_weights
    )
    assert weights_loaded == {str(i): 1 / 9 for i in range(1, 10)}


@pytest.mark.asyncio
async def test_persist_backtest_result_handles_inf_profit_factor(
    bot_status_factory,
) -> None:
    """Infinite profit_factor is coerced to NULL (no losing trades = inf in dataclass)."""
    import math

    session_factory = bot_status_factory
    async with session_factory() as session:
        result = BacktestResult(
            n_trades=1, win_rate=1.0, profit_factor=math.inf, sharpe=0.0,
            max_drawdown=0.0,
            equity_curve=[(datetime(2025, 1, 1, tzinfo=timezone.utc), 10000.0)],
            trade_log=[],
            params_hash="inf_hash",
            initial_balance=10000.0, final_balance=10100.0,
            symbol="BTC/USDT", timeframe="1h",
            start_ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_ts=datetime(2025, 2, 1, tzinfo=timezone.utc),
        )
        new_id = await persist_backtest_result(session, result=result)
        await session.commit()
        row = (await session.execute(
            sa.text("SELECT profit_factor FROM backtests WHERE id = :i"),
            {"i": new_id},
        )).first()
    assert row is not None
    assert row.profit_factor is None
