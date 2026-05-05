"""Runaway gap up — mid-trend gap with strong volume; trend continuation."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class RunawayGapUpPattern:
    pattern_id = "runaway_gap_up"
    pattern_type = "chart"
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
        prior_high = float(bars["high"].iloc[current_idx - 1])
        if cur_open <= prior_high:
            return None
        # Gap size ≥ 0.5 ATR
        if cur_open - prior_high < 0.5 * atr:
            return None
        # Mid-trend: prior 20 bars trended up
        prior_close = float(bars["close"].iloc[current_idx - 20])
        if (prior_high - prior_close) < 2 * atr:
            return None
        # Volume surge (≥ 1.5x average)
        vol = float(bars["volume"].iloc[current_idx])
        avg_vol = float(win["volume"].iloc[:-1].mean())
        if avg_vol == 0 or vol < 1.5 * avg_vol:
            return None
        if cur_close < cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "gap_size": cur_open - prior_high,
                "vol_ratio": vol / avg_vol,
                "atr": atr,
            },
        )
