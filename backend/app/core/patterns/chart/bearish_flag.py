"""Bearish flag — strong downmove (pole) followed by tight upward consolidation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class BearishFlagPattern:
    pattern_id: str = "bearish_flag"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        closes = win["close"].to_numpy(dtype=float)
        n = len(win)
        pole = closes[: n // 2]
        flag = closes[n // 2 :]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        pole_loss = pole[0] - pole[-1]
        if pole_loss < 5 * atr:
            return None
        flag_slope, _ = np.polyfit(np.arange(len(flag), dtype=float), flag, 1)
        if flag_slope < 0:
            return None
        if (flag.max() - flag.min()) > 0.5 * (pole.max() - pole.min()):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.75,
            confidence=0.65,
            evidence={
                "pole_loss": float(pole_loss),
                "flag_slope": float(flag_slope),
                "atr": atr,
            },
        )
