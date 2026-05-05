"""Expanding triangle — three peaks each higher AND three troughs each lower."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class ExpandingTrianglePattern:
    pattern_id: str = "expanding_triangle"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

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
        peaks = find_swing_highs(highs, prominence=prom_h, distance=4)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=4)
        if len(peaks) < 3 or len(troughs) < 3:
            return None
        peak_vals = highs[peaks[-3:]]
        trough_vals = lows[troughs[-3:]]
        if not (peak_vals[0] < peak_vals[1] < peak_vals[2]):
            return None
        if not (trough_vals[0] > trough_vals[1] > trough_vals[2]):
            return None
        # Direction: signal direction of resolved breakout (current close vs mean)
        cur_close = float(win["close"].iloc[-1])
        mid = float((peak_vals.mean() + trough_vals.mean()) / 2)
        direction: Direction = "LONG" if cur_close > mid else "SHORT"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.6,
            confidence=0.5,
            evidence={
                "peaks": [float(v) for v in peak_vals],
                "troughs": [float(v) for v in trough_vals],
                "current_close": cur_close,
            },
        )
