"""Exhaustion gap down — big gap down after extended downtrend; bullish reversal."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class ExhaustionGapDownPattern:
    pattern_id: str = "exhaustion_gap_down"
    pattern_type: PatternType = "chart"
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
        _vol_cur  = float(bars["volume"].iloc[current_idx])
        _vol_avg  = float(bars["volume"].iloc[max(0, current_idx - 10):current_idx].mean())
        vol_climax = _vol_avg > 0 and _vol_cur > _vol_avg * 1.3
        confidence = 0.72 if vol_climax else 0.50
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=confidence,
            evidence={
                "gap_size": prior_low - cur_open,
                "trend_loss": prior_close - prior_low,
                "atr": atr,
                "vol_climax": vol_climax,
            },
        )
