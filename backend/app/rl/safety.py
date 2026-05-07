"""SP-4 Phase C — production-side safety guards for the RL brain.

Spec sec 7 — four mandatory guards:

1. **Action smoothing** (production): exponential α=0.3 over the last 3 ticks
   so a flickering brain (borderline logits between two actions) doesn't
   cause back-to-back position flips.

2. **Drawdown circuit-breaker** (production): a 7-day portfolio drawdown
   above 15% auto-pauses the system via SP-PAUSE. Manual operator action
   required to resume — a 15%-DD week needs eyes on it.

3. **Turnover cap penalty** (training-time): trades-per-asset-per-day above
   12 incur a -1.0 reward penalty during PPO training, nudging the policy
   away from hyperactive strategies whose paper P&L wouldn't survive
   real-world slippage.

4. **No leverage** (production): position size never exceeds allocated
   capital. Enforced upstream of this module by the existing Kelly cap;
   reproduced here as a safety constant the inference path asserts on.

The first three live as pure functions; the fourth is just a constant.
This module has zero external state — it's deliberately functional so
the inference path can call it in any order on any tick.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Sequence

from app.rl.replay_buffer import (
    ACTION_FLAT,
    ALL_ACTIONS,
)


# Spec sec 7.1 — turnover cap.
TURNOVER_CAP_PER_DAY: int = 12
TURNOVER_CAP_PENALTY: float = -1.0

# Spec sec 7.2 — drawdown circuit-breaker.
DRAWDOWN_BREAKER_THRESHOLD: float = 0.15
DRAWDOWN_BREAKER_WINDOW_DAYS: int = 7

# Spec sec 7.3 — action smoothing window.
SMOOTHING_WINDOW: int = 3

# Spec sec 7.4 — no leverage.
MAX_POSITION_FRACTION: float = 1.0


@dataclass(frozen=True)
class DrawdownState:
    """Snapshot of recent equity peaks + troughs for the breaker check."""

    peak: float
    trough: float
    drawdown_pct: float
    triggered: bool


def smooth_action(history: Sequence[str], raw_action: str) -> str:
    """Mode-of-last-N action smoothing. Ties break toward FLAT.

    ``history`` is the immutable trailing list of raw actions (oldest
    first). ``raw_action`` is the action the brain just emitted; this
    function does NOT mutate history — caller appends after deciding
    what to do with the smoothed result.

    Returns the smoothed action: the most-frequent action across
    [history-tail-of-(SMOOTHING_WINDOW-1), raw_action]. With
    SMOOTHING_WINDOW=3 the window includes raw_action + 2 prior.

    Edge cases:
    * empty history → returns raw_action verbatim (no signal to smooth)
    * tied counts → FLAT wins (most conservative tiebreaker)
    """
    if raw_action not in ALL_ACTIONS:
        raise ValueError(
            f"unknown raw_action {raw_action!r}; expected one of {ALL_ACTIONS}"
        )
    if not history:
        return raw_action

    # Last (SMOOTHING_WINDOW - 1) prior actions + the new one.
    tail = list(history)[-(SMOOTHING_WINDOW - 1):]
    window = tail + [raw_action]

    counts = Counter(window)
    max_count = max(counts.values())
    winners = [a for a, c in counts.items() if c == max_count]
    if len(winners) == 1:
        return winners[0]
    # Tie → prefer FLAT, else fall back to raw_action.
    if ACTION_FLAT in winners:
        return ACTION_FLAT
    return raw_action


def evaluate_drawdown(
    equity_series: Sequence[float],
    *,
    threshold: float = DRAWDOWN_BREAKER_THRESHOLD,
) -> DrawdownState:
    """Compute peak-to-trough drawdown over the supplied equity series.

    ``equity_series`` is an ordered list of portfolio equity snapshots
    (oldest first); caller is responsible for windowing it to the last
    ``DRAWDOWN_BREAKER_WINDOW_DAYS`` of data before passing in.

    Returns the peak / trough / drawdown_pct + whether the breaker
    should fire. ``triggered=True`` ↔ ``drawdown_pct >= threshold``.

    An empty or single-point series is never considered to be in
    drawdown (no signal yet).
    """
    if len(equity_series) < 2:
        return DrawdownState(peak=0.0, trough=0.0, drawdown_pct=0.0, triggered=False)
    peak = equity_series[0]
    trough = equity_series[0]
    max_dd = 0.0
    cur_peak = peak
    for v in equity_series:
        if v > cur_peak:
            cur_peak = v
        dd = (cur_peak - v) / cur_peak if cur_peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            peak = cur_peak
            trough = v
    return DrawdownState(
        peak=float(peak),
        trough=float(trough),
        drawdown_pct=float(max_dd),
        triggered=bool(max_dd >= threshold),
    )


def turnover_cap_penalty(
    n_trades_today: int, *, cap: int = TURNOVER_CAP_PER_DAY,
    penalty: float = TURNOVER_CAP_PENALTY,
) -> float:
    """Return the per-trade penalty for episodes that exceed the cap.

    Returns 0.0 if under cap (no penalty); ``penalty`` (default -1.0) if
    at-or-over cap. The trainer adds this to every transition in an
    over-cap day's batch — not just the marginal trades — so the policy
    learns the whole-day pattern is bad, not just the last trade.
    """
    if n_trades_today <= cap:
        return 0.0
    return float(penalty)


def assert_no_leverage(position_fraction: float) -> None:
    """Defensive — raise if a caller would size above MAX_POSITION_FRACTION.

    SP-4 v1 stays conservative; SP-8 (real-money mode) will revisit
    leverage with hardware-confirm safeguards.
    """
    if position_fraction < 0.0:
        raise ValueError(
            f"position_fraction must be >= 0, got {position_fraction}"
        )
    if position_fraction > MAX_POSITION_FRACTION:
        raise ValueError(
            f"position_fraction {position_fraction} exceeds "
            f"no-leverage cap {MAX_POSITION_FRACTION}"
        )


def update_history_inplace(history: deque[str], action: str) -> None:
    """Helper: append ``action`` to ``history`` and trim to SMOOTHING_WINDOW.

    Operator passes a ``collections.deque(maxlen=SMOOTHING_WINDOW)`` and
    we just call ``.append(...)``; the maxlen handles trimming. This
    helper exists mainly for symmetry with smooth_action — callers
    should be encouraged to use a maxlen-bounded deque to begin with.
    """
    history.append(action)


__all__ = [
    "DRAWDOWN_BREAKER_THRESHOLD",
    "DRAWDOWN_BREAKER_WINDOW_DAYS",
    "MAX_POSITION_FRACTION",
    "SMOOTHING_WINDOW",
    "TURNOVER_CAP_PENALTY",
    "TURNOVER_CAP_PER_DAY",
    "DrawdownState",
    "assert_no_leverage",
    "evaluate_drawdown",
    "smooth_action",
    "turnover_cap_penalty",
    "update_history_inplace",
]
