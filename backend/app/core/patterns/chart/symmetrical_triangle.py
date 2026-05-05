"""Symmetrical triangle — both lines converging; fire on breakout direction."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class SymmetricalTrianglePattern:
    pattern_id: str = "symmetrical_triangle"
    pattern_type: PatternType = "chart"
    LOOKBACK = 50

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
        peaks = find_swing_highs(highs, prominence=prom_h, distance=3)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=3)
        if len(peaks) < 2 or len(troughs) < 2:
            return None
        s_high, _ = np.polyfit(
            np.array(peaks, dtype=float), highs[peaks], 1
        )
        s_low, _ = np.polyfit(
            np.array(troughs, dtype=float), lows[troughs], 1
        )
        # Convergent: peaks trending down, troughs trending up
        if s_high >= 0 or s_low <= 0:
            return None
        # Direction: breakout direction = current bar's close vs window mid-price
        mid_price = float((highs.mean() + lows.mean()) / 2)
        cur_close = float(win["close"].iloc[-1])
        cur_high = float(win["high"].iloc[-1])
        cur_low = float(win["low"].iloc[-1])
        # Need a clear break — close beyond ±0.5% of window mid-range
        if cur_close > mid_price * 1.005 and cur_close > cur_low:
            direction: Direction = "LONG"
        elif cur_close < mid_price * 0.995 and cur_close < cur_high:
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.65,
            confidence=0.55,
            evidence={
                "high_slope": float(s_high),
                "low_slope": float(s_low),
                "mid_price": mid_price,
                "current_close": cur_close,
            },
        )
