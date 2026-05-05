"""Bullish pennant — pole + symmetric triangle consolidation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class BullishPennantPattern:
    pattern_id = "bullish_pennant"
    pattern_type = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        closes = win["close"].to_numpy(dtype=float)
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        n = len(win)
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        pole_gain = closes[n // 2] - closes[0]
        if pole_gain < 5 * atr:
            return None
        # Pennant region (second half): highs descending, lows ascending
        xs = np.arange(n - n // 2, dtype=float)
        s_h, _ = np.polyfit(xs, highs[n // 2 :], 1)
        s_l, _ = np.polyfit(xs, lows[n // 2 :], 1)
        if s_h >= 0 or s_l <= 0:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "pole_gain": float(pole_gain),
                "high_slope": float(s_h),
                "low_slope": float(s_l),
            },
        )
