"""Falling wedge — both lines falling but resistance steeper (bullish)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class FallingWedgePattern:
    pattern_id: str = "falling_wedge"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        prom_h = max(float(highs.std()) * 0.15, 0.5)
        prom_l = max(float(lows.std()) * 0.15, 0.5)
        peaks = find_swing_highs(highs, prominence=prom_h, distance=4)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=4)
        if len(peaks) < 2 or len(troughs) < 2:
            return None
        s_h, _ = np.polyfit(np.array(peaks, dtype=float), highs[peaks], 1)
        s_l, _ = np.polyfit(np.array(troughs, dtype=float), lows[troughs], 1)
        # Both falling, resistance steeper (more negative)
        if s_h >= 0 or s_l >= 0 or s_h >= s_l:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "high_slope": float(s_h),
                "low_slope": float(s_l),
            },
        )
