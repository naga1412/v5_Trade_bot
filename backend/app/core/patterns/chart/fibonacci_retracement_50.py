"""Fibonacci retracement 50% — signal at 50% retracement of a recent swing."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class FibonacciRetracement50Pattern:
    pattern_id = "fibonacci_retracement_50"
    pattern_type = "chart"
    LOOKBACK = 80
    TOL = 0.02  # ±2% around 50%

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
        level_50 = (swing_high + swing_low) / 2
        if abs(cur_close - level_50) / level_50 > self.TOL:
            return None
        # If peak came AFTER trough → up swing, expect bounce LONG.
        # If trough came AFTER peak → down swing, expect rejection SHORT.
        direction = "LONG" if last_peak > last_trough else "SHORT"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.55,
            confidence=0.5,
            evidence={
                "swing_high": swing_high,
                "swing_low": swing_low,
                "level_50": level_50,
                "current_close": cur_close,
            },
        )
