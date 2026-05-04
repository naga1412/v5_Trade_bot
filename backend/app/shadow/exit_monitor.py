from dataclasses import dataclass
from enum import Enum

from app.shadow.engine import ShadowPosition, Direction


TIMEOUT_BARS: int = 24  # 24 hourly bars = 24h


class ExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    exit_price: float


def check_exit(
    pos: ShadowPosition, *, bar_high: float, bar_low: float, bar_close: float
) -> ExitDecision | None:
    """Check whether this candle triggers a close.

    Convention (matches paper engine): if both SL and TP touched in same bar,
    assume SL hit first (pessimistic).
    """
    # Timeout fires regardless of SL/TP if bars_held >= TIMEOUT_BARS
    if pos.bars_held >= TIMEOUT_BARS:
        return ExitDecision(reason=ExitReason.TIMEOUT, exit_price=bar_close)

    if pos.direction is Direction.LONG:
        sl_hit = bar_low <= pos.stop_loss
        tp_hit = bar_high >= pos.take_profit
        if sl_hit:
            return ExitDecision(reason=ExitReason.STOP_LOSS, exit_price=pos.stop_loss)
        if tp_hit:
            return ExitDecision(reason=ExitReason.TAKE_PROFIT, exit_price=pos.take_profit)
        return None

    # SHORT
    sl_hit = bar_high >= pos.stop_loss
    tp_hit = bar_low <= pos.take_profit
    if sl_hit:
        return ExitDecision(reason=ExitReason.STOP_LOSS, exit_price=pos.stop_loss)
    if tp_hit:
        return ExitDecision(reason=ExitReason.TAKE_PROFIT, exit_price=pos.take_profit)
    return None
