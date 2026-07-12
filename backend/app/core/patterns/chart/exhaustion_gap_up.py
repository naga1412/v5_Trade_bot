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
        _vol_cur  = float(bars["volume"].iloc[current_idx])
        _vol_avg  = float(bars["volume"].iloc[max(0, current_idx - 10):current_idx].mean())
        vol_climax = _vol_avg > 0 and _vol_cur > _vol_avg * 1.3
        confidence = 0.72 if vol_climax else 0.50
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=confidence,
            evidence={
                "gap_size": cur_open - prior_high,
                "trend_gain": prior_high - prior_close,
                "atr": atr,
                "vol_climax": vol_climax,
            },
        )
