"""Mean reversion to MA — extended deviation from 50-bar SMA reverses toward it.

Direction: LONG if price is below MA, SHORT if above (revert toward).
Fires only when |close - SMA| > 2 × ATR(14).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class MeanReversionToMaPattern:
    pattern_id = "mean_reversion_to_ma"
    pattern_type = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        sma = float(
            bars["close"].iloc[current_idx - 49 : current_idx + 1].mean()
        )
        cur_close = float(bars["close"].iloc[current_idx])
        deviation = cur_close - sma
        if abs(deviation) < 2 * atr:
            return None
        direction = "SHORT" if deviation > 0 else "LONG"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.6,
            confidence=0.5,
            evidence={
                "sma": sma,
                "current_close": cur_close,
                "deviation": deviation,
                "atr": atr,
            },
        )
