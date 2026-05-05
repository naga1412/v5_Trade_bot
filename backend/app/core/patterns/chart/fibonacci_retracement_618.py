"""Fibonacci retracement 61.8% — golden-ratio retracement of a recent swing."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class FibonacciRetracement618Pattern:
    pattern_id: str = "fibonacci_retracement_618"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80
    TOL = 0.02

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        prom_h = max(float(highs.std()) * 0.2, 0.5)
        prom_l = max(float(lows.std()) * 0.2, 0.5)
        peaks = find_swing_highs(highs, prominence=prom_h, distance=5)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=5)
        if not peaks or not troughs:
            return None
        last_peak = peaks[-1]
        last_trough = troughs[-1]
        cur_close = float(win["close"].iloc[-1])
        swing_high = float(highs[last_peak])
        swing_low = float(lows[last_trough])
        if swing_high <= swing_low:
            return None
        if last_peak > last_trough:
            # Up move (low → high). 61.8% retrace = high - 0.618 × range. LONG bounce.
            level = swing_high - 0.618 * (swing_high - swing_low)
            direction: Direction = "LONG"
        else:
            # Down move (high → low). 61.8% retrace up = low + 0.618 × range. SHORT.
            level = swing_low + 0.618 * (swing_high - swing_low)
            direction = "SHORT"
        if abs(cur_close - level) / level > self.TOL:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.6,
            confidence=0.55,
            evidence={
                "swing_high": swing_high,
                "swing_low": swing_low,
                "level_618": level,
                "current_close": cur_close,
            },
        )
