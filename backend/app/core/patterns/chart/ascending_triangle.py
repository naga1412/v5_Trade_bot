"""Ascending triangle — flat resistance + rising support, bullish breakout."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class AscendingTrianglePattern:
    pattern_id = "ascending_triangle"
    pattern_type = "chart"
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
        peak_vals = highs[peaks]
        trough_vals = lows[troughs]
        # Flat resistance: peak heights within 1.5%
        if (peak_vals.max() - peak_vals.min()) / peak_vals.max() > 0.015:
            return None
        # Rising support: troughs trending up
        slope, _ = np.polyfit(np.array(troughs, dtype=float), trough_vals, 1)
        if slope <= 0:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "resistance_level": float(peak_vals.mean()),
                "support_slope": float(slope),
                "n_peaks": len(peaks),
                "n_troughs": len(troughs),
            },
        )
