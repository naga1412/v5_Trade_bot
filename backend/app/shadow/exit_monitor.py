from dataclasses import dataclass
from enum import Enum

from app.shadow.engine import ShadowPosition, Direction


# PR3 Phase 5 (spec §4.6): per-TF exit timeout. Equal ~24h wall-clock
# holdtime across TFs: 1h → 24 bars, 15m → 96 bars. KeyError on unknown
# TF is a programming-error fail-loud — new TFs require an explicit
# entry here AND a matching entry in HOLD_TP_SCALING_TABLE for G1.
TIMEOUT_BARS_PER_TF: dict[str, int] = {
    "1h": 24,
    "15m": 96,
}

# Legacy public name preserved for callers that imported it pre-PR3.
# Equal to TIMEOUT_BARS_PER_TF["1h"]; deprecation candidates can switch
# to the per-TF dict in their own time.
TIMEOUT_BARS: int = TIMEOUT_BARS_PER_TF["1h"]


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

    PR3: the timeout limit is read per-TF from
    ``TIMEOUT_BARS_PER_TF[pos.timeframe]``. Unknown TF raises KeyError
    (programming-error fail-loud per spec §4.6 — new TFs require an
    explicit entry here AND in HOLD_TP_SCALING_TABLE for G1).
    """
    # Phase 5.5.4: G1 scaling override. When pos.hold_timeout_bars is set
    # (HOLD_TP_SCALING_ENABLED=True at open-time), use the recorded
    # per-position limit; otherwise fall back to the per-TF baseline.
    # Pre-G1 ShadowPosition instances always have hold_timeout_bars=None,
    # so this is bit-identical to the Phase 5 baseline for them.
    if pos.hold_timeout_bars is not None:
        limit = pos.hold_timeout_bars
    else:
        limit = TIMEOUT_BARS_PER_TF[pos.timeframe]  # KeyError → fail-loud
    if pos.bars_held >= limit:
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
