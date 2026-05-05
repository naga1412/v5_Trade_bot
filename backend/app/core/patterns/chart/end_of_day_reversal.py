"""End-of-day reversal — last bar opens with the trend then reverses sharply.

Single-TF approximation: the final bar reverses ≥ 1 ATR against the prior 5-bar drift.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class EndOfDayReversalPattern:
    pattern_id = "end_of_day_reversal"
    pattern_type = "chart"
    LOOKBACK = 20

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        cur = bars.iloc[current_idx]
        # Prior drift: close[-2] - close[-6]
        recent_close = float(bars["close"].iloc[current_idx - 1])
        old_close = float(bars["close"].iloc[current_idx - 5])
        prior_drift = recent_close - old_close
        cur_body = float(cur["close"] - cur["open"])
        # Drift up but current bar is bearish ≥ 1 ATR → SHORT reversal
        if prior_drift > 0.5 * atr and cur_body < -atr:
            direction = "SHORT"
        elif prior_drift < -0.5 * atr and cur_body > atr:
            direction = "LONG"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.65,
            confidence=0.55,
            evidence={
                "prior_drift": prior_drift,
                "current_body": cur_body,
                "atr": atr,
            },
        )
