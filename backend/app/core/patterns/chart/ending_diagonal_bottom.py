"""Ending diagonal bottom — Elliott-style wedge at the end of a downtrend."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class EndingDiagonalBottomPattern:
    pattern_id = "ending_diagonal_bottom"
    pattern_type = "chart"
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
        if len(peaks) < 2 or len(troughs) < 3:
            return None
        s_h, _ = np.polyfit(np.array(peaks, dtype=float), highs[peaks], 1)
        s_l, _ = np.polyfit(np.array(troughs, dtype=float), lows[troughs], 1)
        if s_h >= 0 or s_l >= 0 or s_h >= s_l:
            return None
        if not (lows[troughs[0]] > lows[troughs[1]] > lows[troughs[-1]]):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.65,
            confidence=0.55,
            evidence={
                "high_slope": float(s_h),
                "low_slope": float(s_l),
                "n_troughs": len(troughs),
            },
        )
