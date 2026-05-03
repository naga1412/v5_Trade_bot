from datetime import datetime, timezone

from app.shadow.engine import ShadowPosition, Direction
from app.shadow.exit_monitor import (
    ExitReason,
    check_exit,
    TIMEOUT_BARS,
)


def make_long_pos(*, entry: float = 100.0, sl: float = 95.0, tp: float = 110.0,
                  bars_held: int = 0) -> ShadowPosition:
    ts = datetime(2026, 5, 3, tzinfo=timezone.utc)
    return ShadowPosition(
        symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        position_size_usdt=30.0, entry_score=0.6, entry_confidence=0.7,
        entry_atr=3.33, layer_scores={"1": 0.6}, bars_held=bars_held,
        opened_at=ts, last_check_at=ts, signal_id="test1",
    )


def test_long_hits_tp_first() -> None:
    pos = make_long_pos()
    decision = check_exit(pos, bar_high=112.0, bar_low=98.0, bar_close=111.0)
    assert decision is not None
    assert decision.reason is ExitReason.TAKE_PROFIT
    assert decision.exit_price == 110.0


def test_long_hits_sl_first() -> None:
    pos = make_long_pos()
    decision = check_exit(pos, bar_high=101.0, bar_low=94.0, bar_close=96.0)
    assert decision is not None
    assert decision.reason is ExitReason.STOP_LOSS
    assert decision.exit_price == 95.0


def test_long_both_sl_and_tp_in_same_bar_pessimistic_assumes_sl() -> None:
    """Same convention as paper engine: assume SL first when both touched."""
    pos = make_long_pos()
    decision = check_exit(pos, bar_high=112.0, bar_low=94.0, bar_close=100.0)
    assert decision is not None
    assert decision.reason is ExitReason.STOP_LOSS


def test_long_no_exit_when_in_range() -> None:
    pos = make_long_pos()
    decision = check_exit(pos, bar_high=109.0, bar_low=96.0, bar_close=100.0)
    assert decision is None


def test_short_hits_tp_first() -> None:
    pos = make_long_pos(entry=100, sl=105, tp=90)
    pos.direction = Direction.SHORT
    decision = check_exit(pos, bar_high=101.0, bar_low=89.0, bar_close=92.0)
    assert decision is not None
    assert decision.reason is ExitReason.TAKE_PROFIT
    assert decision.exit_price == 90.0


def test_short_hits_sl_first() -> None:
    pos = make_long_pos(entry=100, sl=105, tp=90)
    pos.direction = Direction.SHORT
    decision = check_exit(pos, bar_high=106.0, bar_low=99.0, bar_close=104.0)
    assert decision is not None
    assert decision.reason is ExitReason.STOP_LOSS
    assert decision.exit_price == 105.0


def test_timeout_after_max_bars() -> None:
    pos = make_long_pos(bars_held=TIMEOUT_BARS)  # already at timeout
    decision = check_exit(pos, bar_high=109.0, bar_low=98.0, bar_close=100.5)
    assert decision is not None
    assert decision.reason is ExitReason.TIMEOUT
    assert decision.exit_price == 100.5  # close at this candle's close
