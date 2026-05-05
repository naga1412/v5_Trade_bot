"""Exhaustion gap up — big gap up after extended uptrend; bearish reversal."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class ExhaustionGapUpPattern:
    pattern_id: str = "exhaustion_gap_up"
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
        prior_high = float(bars["high"].iloc[current_idx - 1])
        # Big gap up (≥ 1 ATR)
        if cur_open <= prior_high + 1.0 * atr:
            return None
        # Extended uptrend: cumulative gain ≥ 4 ATR over 40 bars
        prior_close = float(bars["close"].iloc[current_idx - 40])
        if (prior_high - prior_close) < 4 * atr:
            return None
        # Bearish reversal: close below open
        if cur_close >= cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=0.55,
            evidence={
                "gap_size": cur_open - prior_high,
                "trend_gain": prior_high - prior_close,
                "atr": atr,
            },
        )
