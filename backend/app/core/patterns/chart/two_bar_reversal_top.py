"""Two-bar reversal top — strong up bar followed by equally strong down bar."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class TwoBarReversalTopPattern:
    pattern_id: str = "two_bar_reversal_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 20

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        prev_body = float(prev["close"] - prev["open"])
        cur_body = float(cur["close"] - cur["open"])
        # Prior bullish bar > 1 ATR; current bearish bar > 1 ATR
        if prev_body < atr or cur_body > -atr:
            return None
        # Roughly equal magnitudes
        if abs(prev_body + cur_body) > 0.3 * atr:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=0.6,
            evidence={
                "prev_body": prev_body,
                "cur_body": cur_body,
                "atr": atr,
            },
        )
