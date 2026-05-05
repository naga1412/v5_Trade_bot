"""Double gap continuation up — two consecutive gap-ups confirming an uptrend."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class DoubleGapContinuationUpPattern:
    pattern_id: str = "double_gap_continuation_up"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        cur_open = float(bars["open"].iloc[current_idx])
        prior_high = float(bars["high"].iloc[current_idx - 1])
        prior_open = float(bars["open"].iloc[current_idx - 1])
        prev_high = float(bars["high"].iloc[current_idx - 2])
        # Both bars gapped up
        if cur_open <= prior_high + 0.3 * atr:
            return None
        if prior_open <= prev_high + 0.3 * atr:
            return None
        cur_close = float(bars["close"].iloc[current_idx])
        if cur_close < cur_open:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "gap1": prior_open - prev_high,
                "gap2": cur_open - prior_high,
                "atr": atr,
            },
        )
