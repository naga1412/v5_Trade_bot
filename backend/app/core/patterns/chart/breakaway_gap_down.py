"""Breakaway gap down — gap below prior consolidation low."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class BreakawayGapDownPattern:
    pattern_id = "breakaway_gap_down"
    pattern_type = "chart"
    LOOKBACK = 50

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        prior_window_low = float(
            bars["low"].iloc[current_idx - self.LOOKBACK : current_idx].min()
        )
        cur_open = float(bars["open"].iloc[current_idx])
        cur_close = float(bars["close"].iloc[current_idx])
        if cur_open >= prior_window_low - 1.0 * atr:
            return None
        if cur_close > cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.75,
            confidence=0.65,
            evidence={
                "prior_window_low": prior_window_low,
                "current_open": cur_open,
                "current_close": cur_close,
                "atr": atr,
            },
        )
