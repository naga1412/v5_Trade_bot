"""Layer 4 - Smart Money Concepts (SP-5 spec section 3 decision 3).

Five sub-detectors, each emitting +1 (bullish), -1 (bearish), or 0:

  1. BOS (Break of Structure) - current close pierces the most-recent swing
     high (bullish BOS) or swing low (bearish BOS).
  2. CHoCH (Change of Character) - first opposite-direction swing after a
     run of same-direction swings (trend reversal hint).
  3. Order Block - large opposite-color bar that price has now revisited.
     Implementation note: order-block geometry is fuzzy in the literature;
     we use the simplest reasonable variant (body >= 1.5 * ATR, last_close
     inside the body) and accept that real OB tagging requires manual
     annotation; refine in a follow-up if false-positive rate is high.
  4. Fair Value Gap (FVG) - three-bar imbalance: bar[i-2].high < bar[i].low
     (bullish FVG) or bar[i-2].low > bar[i].high (bearish FVG).
  5. Liquidity Sweep - wick pierces a prior swing high/low and closes back
     inside the range (stop-hunt signal).

The five votes are summed and tanh-squashed to a [-1, +1] score; direction
follows the sign with a 0.05 NEUTRAL band. ``confidence`` is
``0.5 + 0.1 * n_active`` capped at 0.9.
"""
from __future__ import annotations

import math

import pandas as pd

from app.core.patterns.chart._helpers import (
    find_swing_highs,
    find_swing_lows,
    recent_atr,
)
from app.core.scoring.types import Direction, LayerScore

LOOKBACK: int = 60
NEUTRAL_BAND: float = 0.05


def _bos_vote(bars: pd.DataFrame, current_idx: int) -> int:
    """Vote +1 if last close breaks the most recent swing high (or rolling-max fallback)."""
    win = bars.iloc[max(0, current_idx - LOOKBACK + 1) : current_idx + 1]
    highs = win["high"].to_numpy(dtype=float)
    lows = win["low"].to_numpy(dtype=float)
    closes = win["close"].to_numpy(dtype=float)
    last_close = float(closes[-1])
    sw_highs = find_swing_highs(highs[:-1], prominence=0.5, distance=3)
    sw_lows = find_swing_lows(lows[:-1], prominence=0.5, distance=3)
    if sw_highs and last_close > float(highs[sw_highs[-1]]):
        return 1
    if sw_lows and last_close < float(lows[sw_lows[-1]]):
        return -1
    # Fallback for trending series with no classical pivots: compare the
    # last close against the prior 20 closes' max/min (a simple range break).
    if closes.shape[0] >= 21:
        prior = closes[:-1][-20:]
        if last_close > float(prior.max()):
            return 1
        if last_close < float(prior.min()):
            return -1
    return 0


def _choch_vote(bars: pd.DataFrame, current_idx: int) -> int:
    win = bars.iloc[max(0, current_idx - LOOKBACK + 1) : current_idx + 1]
    closes = win["close"].to_numpy(dtype=float)
    if closes.shape[0] < 20:
        return 0
    sw_highs = find_swing_highs(closes, prominence=0.3, distance=3)
    sw_lows = find_swing_lows(closes, prominence=0.3, distance=3)
    if len(sw_highs) >= 2 and closes[sw_highs[-1]] < closes[sw_highs[-2]]:
        return -1  # lower high after rising swing -> bearish CHoCH
    if len(sw_lows) >= 2 and closes[sw_lows[-1]] > closes[sw_lows[-2]]:
        return 1   # higher low after falling swing -> bullish CHoCH
    return 0


def _order_block_vote(bars: pd.DataFrame, current_idx: int) -> int:
    """Find a recent large opposite-color bar that price has revisited."""
    win = bars.iloc[max(0, current_idx - 30) : current_idx + 1]
    if len(win) < 5:
        return 0
    atr = recent_atr(bars, current_idx, period=14)
    if atr <= 0:
        return 0
    last_close = float(win["close"].iloc[-1])
    for i in range(len(win) - 5, max(0, len(win) - 25), -1):
        body = abs(float(win["close"].iloc[i]) - float(win["open"].iloc[i]))
        if body < 1.5 * atr:
            continue
        is_bear = float(win["close"].iloc[i]) < float(win["open"].iloc[i])
        block_high = max(float(win["open"].iloc[i]), float(win["close"].iloc[i]))
        block_low = min(float(win["open"].iloc[i]), float(win["close"].iloc[i]))
        if is_bear and block_low <= last_close <= block_high:
            return 1   # price returned to bearish OB -> buyers may absorb
        if not is_bear and block_low <= last_close <= block_high:
            return -1
    return 0


def _fvg_vote(bars: pd.DataFrame, current_idx: int) -> int:
    if current_idx < 2:
        return 0
    high_2 = float(bars["high"].iloc[current_idx - 2])
    low_2 = float(bars["low"].iloc[current_idx - 2])
    high_now = float(bars["high"].iloc[current_idx])
    low_now = float(bars["low"].iloc[current_idx])
    if high_2 < low_now:
        return 1   # bullish FVG between bar[i-2].high and bar[i].low
    if low_2 > high_now:
        return -1
    return 0


def _liquidity_sweep_vote(bars: pd.DataFrame, current_idx: int) -> int:
    """Detect a stop-hunt: wick clearly pierces a *swing* extreme, then closes back.

    Compares the last bar's wick against actual swing highs/lows in the
    trailing 20 bars (excluding the last bar itself), not the rolling max.
    A monotonic uptrend has no swing highs (find_peaks returns []) so it
    will not register as a sweep — only a real prior-pivot pierce does.
    """
    win = bars.iloc[max(0, current_idx - 20) : current_idx + 1]
    if len(win) < 5:
        return 0
    last_high = float(win["high"].iloc[-1])
    last_low = float(win["low"].iloc[-1])
    last_close = float(win["close"].iloc[-1])
    highs = win["high"].to_numpy(dtype=float)
    lows = win["low"].to_numpy(dtype=float)
    sw_highs = find_swing_highs(highs[:-1], prominence=0.5, distance=3)
    sw_lows = find_swing_lows(lows[:-1], prominence=0.5, distance=3)
    if sw_highs:
        ref_high = float(highs[sw_highs[-1]])
        if last_high > ref_high and last_close < ref_high:
            return -1   # swept buy-side liquidity, closed back below -> bearish
    if sw_lows:
        ref_low = float(lows[sw_lows[-1]])
        if last_low < ref_low and last_close > ref_low:
            return 1
    return 0


def score(bars: pd.DataFrame) -> LayerScore | None:
    if len(bars) < LOOKBACK:
        return None
    current_idx = len(bars) - 1

    votes: dict[str, int] = {
        "bos": _bos_vote(bars, current_idx),
        "choch": _choch_vote(bars, current_idx),
        "ob": _order_block_vote(bars, current_idx),
        "fvg": _fvg_vote(bars, current_idx),
        "sweep": _liquidity_sweep_vote(bars, current_idx),
    }
    raw = sum(votes.values())
    squashed = math.tanh(raw / 3.0)
    n_active = sum(1 for v in votes.values() if v != 0)
    confidence = min(0.9, 0.5 + 0.1 * n_active)

    if abs(squashed) < NEUTRAL_BAND:
        direction = Direction.NEUTRAL
        strength = 0.0
    elif squashed > 0:
        direction = Direction.LONG
        strength = float(squashed)
    else:
        direction = Direction.SHORT
        strength = float(-squashed)

    notes = ",".join(f"{k}{v:+d}" for k, v in votes.items() if v != 0) or "no SMC"
    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=notes,
    )
