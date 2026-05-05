from datetime import datetime, timezone

import pytest

from app.core.execution.paper_engine import PaperEngine
from app.core.execution.types import Signal, ExitReason
from app.core.scoring.types import Direction


def make_signal(direction: Direction = Direction.LONG, **overrides) -> Signal:
    base = dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=direction,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size=0.01, confidence=0.7, reasoning={},
    )
    base.update(overrides)
    return Signal(**base)


def test_open_long_position_creates_open_trade() -> None:
    engine = PaperEngine()
    opened = engine.on_signal(make_signal())
    assert opened is True
    assert engine.open_position("BTC/USDT") is not None


def test_existing_position_blocks_new_open() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    again = engine.on_signal(make_signal())
    assert again is False


def test_long_closes_on_stop_loss_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 13, tzinfo=timezone.utc),
        high=101.0, low=94.0, close=96.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.STOP_LOSS
    assert closed.exit_price == 95.0
    assert closed.pnl_pct == pytest.approx(-5.0)


def test_long_closes_on_take_profit_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
        high=112.0, low=99.0, close=109.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.TAKE_PROFIT
    assert closed.exit_price == 110.0


def test_short_closes_on_take_profit_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal(
        direction=Direction.SHORT,
        entry_price=100.0, stop_loss=105.0, take_profit=90.0,
    ))
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
        high=101.0, low=89.0, close=92.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.TAKE_PROFIT
    assert closed.exit_price == 90.0


def test_when_both_sl_and_tp_in_same_bar_pessimistic_assumes_sl() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 15, tzinfo=timezone.utc),
        high=112.0, low=94.0, close=100.0,  # both touched
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.STOP_LOSS


def test_bar_without_position_returns_none() -> None:
    engine = PaperEngine()
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
        high=110, low=90, close=100,
    )
    assert closed is None


def test_bars_held_counts_correctly() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal(ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc)))
    engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 13, tzinfo=timezone.utc),
                  high=101, low=99, close=100)
    engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
                  high=101, low=99, close=100)
    closed = engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 15, tzinfo=timezone.utc),
                           high=112, low=99, close=109)
    assert closed is not None
    assert closed.bars_held == 3
