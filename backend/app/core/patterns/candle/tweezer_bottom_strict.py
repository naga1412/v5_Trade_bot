"""Tweezer bottom strict — two consecutive bars with matched lows and opposite colors.

Symmetric counterpart to :mod:`tweezer_top_strict`:
- lows of the two bars match within 0.05 %
- prior bar is bearish (close < open)
- current bar is bullish (close > open)
- direction = LONG (bullish reversal at the matched bottom)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import is_bearish, is_bullish

_LOW_TOLERANCE = 0.0005  # 0.05 %


class TweezerBottomStrictPattern:
    pattern_id: str = "tweezer_bottom_strict"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (is_bearish(prev) and is_bullish(cur)):
            return None
        max_low = max(float(prev["low"]), float(cur["low"]))
        if max_low <= 0:
            return None
        diff_pct = abs(float(prev["low"]) - float(cur["low"])) / max_low
        if diff_pct > _LOW_TOLERANCE:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=min(1.0, 1.0 - diff_pct / _LOW_TOLERANCE),
            confidence=0.75,
            evidence={
                "prev_low": float(prev["low"]),
                "cur_low": float(cur["low"]),
                "diff_pct": float(diff_pct),
            },
        )
