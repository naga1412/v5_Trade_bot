"""Bullish flag — strong upmove (pole) followed by tight downward consolidation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class BullishFlagPattern:
    pattern_id = "bullish_flag"
    pattern_type = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        closes = win["close"].to_numpy(dtype=float)
        n = len(win)
        # First half = pole (must rise > 5%); second half = flag (small downward drift)
        pole = closes[: n // 2]
        flag = closes[n // 2 :]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        pole_gain = pole[-1] - pole[0]
        if pole_gain < 5 * atr:
            return None
        # Flag must drift slightly down or be flat
        flag_slope, _ = np.polyfit(np.arange(len(flag), dtype=float), flag, 1)
        if flag_slope > 0:
            return None
        # Flag range must be smaller than pole range
        if (flag.max() - flag.min()) > 0.5 * (pole.max() - pole.min()):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.75,
            confidence=0.65,
            evidence={
                "pole_gain": float(pole_gain),
                "flag_slope": float(flag_slope),
                "atr": atr,
            },
        )
