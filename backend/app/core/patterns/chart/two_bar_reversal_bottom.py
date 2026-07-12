"""Two-bar reversal bottom — strong down bar followed by equally strong up bar."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr, volume_climax_at_point


class TwoBarReversalBottomPattern:
    pattern_id: str = "two_bar_reversal_bottom"
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
        if prev_body > -atr or cur_body < atr:
            return None
        if abs(prev_body + cur_body) > 0.3 * atr:
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
                "prev_body": prev_body,
                "cur_body": cur_body,
                "atr": atr,
                "vol_climax": vol_climax,
            },
        )
