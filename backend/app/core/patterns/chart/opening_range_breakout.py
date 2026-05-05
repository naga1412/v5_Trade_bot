"""Opening range breakout — price breaks the first-N-bars range of the window.

Single-TF approximation: take the first 5 bars of the lookback window as the
"opening range" and signal when the current close clears it by ≥ 0.3 ATR.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class OpeningRangeBreakoutPattern:
    pattern_id: str = "opening_range_breakout"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        opening = win.iloc[:5]
        or_high = float(opening["high"].max())
        or_low = float(opening["low"].min())
        cur_close = float(win["close"].iloc[-1])
        if cur_close > or_high + 0.3 * atr:
            direction: Direction = "LONG"
        elif cur_close < or_low - 0.3 * atr:
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.65,
            confidence=0.55,
            evidence={
                "or_high": or_high,
                "or_low": or_low,
                "current_close": cur_close,
                "atr": atr,
            },
        )
