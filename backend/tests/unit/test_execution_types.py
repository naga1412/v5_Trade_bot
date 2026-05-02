import pytest
from datetime import datetime, timezone

from app.core.execution.types import Signal, Trade, ExitReason
from app.core.scoring.types import Direction


def test_signal_has_entry_sl_tp_size() -> None:
    sig = Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size=0.01, confidence=0.7, reasoning={},
    )
    assert sig.risk_reward == pytest.approx(2.0)


def test_signal_neutral_direction_rejected() -> None:
    with pytest.raises(ValueError):
        Signal(
            symbol="BTC/USDT", timeframe="1h",
            ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
            direction=Direction.NEUTRAL,
            entry_price=100, stop_loss=95, take_profit=110,
            position_size=0.01, confidence=0.7, reasoning={},
        )


def test_short_signal_risk_reward() -> None:
    sig = Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
        direction=Direction.SHORT,
        entry_price=100.0, stop_loss=105.0, take_profit=90.0,
        position_size=0.01, confidence=0.8, reasoning={},
    )
    assert sig.risk_reward == pytest.approx(2.0)


def test_trade_pnl_pct_long() -> None:
    t = Trade(
        symbol="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, take_profit=110.0, position_size=0.01,
        opened_at=datetime(2026,5,1, tzinfo=timezone.utc),
        closed_at=datetime(2026,5,1,5, tzinfo=timezone.utc),
        bars_held=5, exit_reason=ExitReason.TAKE_PROFIT, reasoning={},
    )
    assert t.pnl_pct == pytest.approx(10.0)


def test_trade_pnl_pct_short() -> None:
    t = Trade(
        symbol="BTC/USDT", direction=Direction.SHORT,
        entry_price=100.0, exit_price=95.0,
        stop_loss=105.0, take_profit=90.0, position_size=0.01,
        opened_at=datetime(2026,5,1, tzinfo=timezone.utc),
        closed_at=datetime(2026,5,1,3, tzinfo=timezone.utc),
        bars_held=3, exit_reason=ExitReason.TAKE_PROFIT, reasoning={},
    )
    assert t.pnl_pct == pytest.approx(5.0)
