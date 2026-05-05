"""Weekly pivot break — close clears the prior 7-day range high (or low).

Single-TF (1h) approximation: 7 days × 24 bars = 168 prior bars define the
"weekly pivot range"; signal when current close breaks it ± 0.3 ATR.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class WeeklyPivotBreakPattern:
    pattern_id: str = "weekly_pivot_break"
    pattern_type: PatternType = "chart"
    LOOKBACK = 168

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        prev_window = bars.iloc[current_idx - self.LOOKBACK : current_idx]
        wk_high = float(prev_window["high"].max())
        wk_low = float(prev_window["low"].min())
        cur_close = float(bars["close"].iloc[current_idx])
        if cur_close > wk_high + 0.3 * atr:
            direction: Direction = "LONG"
        elif cur_close < wk_low - 0.3 * atr:
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.7,
            confidence=0.6,
            evidence={
                "wk_high": wk_high,
                "wk_low": wk_low,
                "current_close": cur_close,
                "atr": atr,
            },
        )
