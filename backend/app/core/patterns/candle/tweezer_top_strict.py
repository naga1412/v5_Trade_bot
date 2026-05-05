"""Tweezer top strict — two consecutive bars with matched highs and opposite colors.

Stricter than the TA-Lib :class:`TweezerTopPattern` wrapper:
- highs of the two bars match within 0.05 %
- prior bar is bullish (close > open)
- current bar is bearish (close < open)
- direction = SHORT (bearish reversal at the matched top)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import is_bearish, is_bullish

_HIGH_TOLERANCE = 0.0005  # 0.05 %


class TweezerTopStrictPattern:
    pattern_id: str = "tweezer_top_strict"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (is_bullish(prev) and is_bearish(cur)):
            return None
        # Highs match within tolerance (use the larger high as the divisor).
        max_high = max(float(prev["high"]), float(cur["high"]))
        if max_high <= 0:
            return None
        diff_pct = abs(float(prev["high"]) - float(cur["high"])) / max_high
        if diff_pct > _HIGH_TOLERANCE:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=min(1.0, 1.0 - diff_pct / _HIGH_TOLERANCE),
            confidence=0.75,
            evidence={
                "prev_high": float(prev["high"]),
                "cur_high": float(cur["high"]),
                "diff_pct": float(diff_pct),
            },
        )
