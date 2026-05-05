"""Rectangle continuation — horizontal range after a directional move."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class RectangleContinuationPattern:
    pattern_id: str = "rectangle_continuation"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

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
        # Pre-rectangle (first 1/3) must show directional move
        pre = closes[: n // 3]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        prior_move = pre[-1] - pre[0]
        if abs(prior_move) < 3 * atr:
            return None
        # Rectangle: highs and lows in horizontal channel (slopes ~ 0)
        rect_highs = highs[n // 3 :]
        rect_lows = lows[n // 3 :]
        xs = np.arange(len(rect_highs), dtype=float)
        s_h, _ = np.polyfit(xs, rect_highs, 1)
        s_l, _ = np.polyfit(xs, rect_lows, 1)
        rect_range = rect_highs.max() - rect_lows.min()
        if rect_range == 0 or abs(s_h) > rect_range / len(xs) or abs(s_l) > rect_range / len(xs):
            return None
        direction: Direction = "LONG" if prior_move > 0 else "SHORT"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.6,
            confidence=0.55,
            evidence={
                "prior_move": float(prior_move),
                "rect_range": float(rect_range),
                "rect_slope_high": float(s_h),
                "rect_slope_low": float(s_l),
            },
        )
