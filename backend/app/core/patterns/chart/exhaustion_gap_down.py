"""Exhaustion gap down — big gap down after extended downtrend; bullish reversal."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class ExhaustionGapDownPattern:
    pattern_id = "exhaustion_gap_down"
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
        cur_open = float(bars["open"].iloc[current_idx])
        cur_close = float(bars["close"].iloc[current_idx])
        prior_low = float(bars["low"].iloc[current_idx - 1])
        if cur_open >= prior_low - 1.0 * atr:
            return None
        prior_close = float(bars["close"].iloc[current_idx - 40])
        if (prior_close - prior_low) < 4 * atr:
            return None
        if cur_close <= cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.55,
            evidence={
                "gap_size": prior_low - cur_open,
                "trend_loss": prior_close - prior_low,
                "atr": atr,
            },
        )
