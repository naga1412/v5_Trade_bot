"""Common gap — small intra-range gap with no special context (neutral / continuation).

Direction follows the gap direction.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class CommonGapPattern:
    pattern_id = "common_gap"
    pattern_type = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        cur = bars.iloc[current_idx]
        prior = bars.iloc[current_idx - 1]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        cur_open = float(cur["open"])
        prior_high = float(prior["high"])
        prior_low = float(prior["low"])
        gap_up = cur_open - prior_high
        gap_down = prior_low - cur_open
        # Common gap: small (between 0.2 and 1 ATR), within range
        if 0.2 * atr < gap_up < 1.0 * atr:
            direction = "LONG"
            gap_size = gap_up
        elif 0.2 * atr < gap_down < 1.0 * atr:
            direction = "SHORT"
            gap_size = gap_down
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.4,
            confidence=0.45,
            evidence={
                "gap_size": float(gap_size),
                "atr": atr,
                "current_open": cur_open,
            },
        )
