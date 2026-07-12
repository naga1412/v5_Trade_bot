"""Descending triangle — flat support + falling resistance, bearish breakout."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class DescendingTrianglePattern:
    pattern_id: str = "descending_triangle"
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
        trough_vals = lows[troughs]
        peak_vals = highs[peaks]
        if (trough_vals.max() - trough_vals.min()) / trough_vals.max() > 0.015:
            return None
        slope, _ = np.polyfit(np.array(peaks, dtype=float), peak_vals, 1)
        if slope >= 0:
            return None
        # Entry trigger: close must break BELOW the flat support.
        support = float(trough_vals.mean())
        close = float(win["close"].iloc[-1])
        if close >= support:
            return None
        breakdown_pct = (support - close) / support
        strength = round(min(0.95, 0.65 + breakdown_pct * 20.0), 2)
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=strength,
            confidence=0.65,
            evidence={
                "support_level": support,
                "resistance_slope": float(slope),
                "breakdown_pct": round(breakdown_pct * 100, 2),
                "n_peaks": len(peaks),
                "n_troughs": len(troughs),
            },
        )
