"""Runaway gap down — mid-downtrend gap with strong volume."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class RunawayGapDownPattern:
    pattern_id: str = "runaway_gap_down"
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
        cur_open = float(bars["open"].iloc[current_idx])
        cur_close = float(bars["close"].iloc[current_idx])
        prior_low = float(bars["low"].iloc[current_idx - 1])
        if cur_open >= prior_low:
            return None
        if prior_low - cur_open < 0.5 * atr:
            return None
        prior_close = float(bars["close"].iloc[current_idx - 20])
        if (prior_close - prior_low) < 2 * atr:
            return None
        vol = float(bars["volume"].iloc[current_idx])
        avg_vol = float(win["volume"].iloc[:-1].mean())
        if avg_vol == 0 or vol < 1.5 * avg_vol:
            return None
        if cur_close > cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=0.6,
            evidence={
                "gap_size": prior_low - cur_open,
                "vol_ratio": vol / avg_vol,
                "atr": atr,
            },
        )
