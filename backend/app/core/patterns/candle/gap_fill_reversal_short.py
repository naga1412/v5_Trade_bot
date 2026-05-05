"""Gap-fill reversal "short" — gap down followed by close above the gap level → LONG.

Symmetric to :mod:`gap_fill_reversal_long`. The suffix ``_short`` describes
the gap *direction* (gap down, the "short" side), not the trade direction.
The detector fires LONG because a filled gap-down rejects the breakdown.

Gates:
- prior bar exists
- current bar opens strictly below prior bar's low (gap down)
- current bar closes above prior bar's close (gap "filled" upward)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class GapFillReversalShortPattern:
    pattern_id: str = "gap_fill_reversal_short"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if float(cur["open"]) >= float(prev["low"]):
            return None
        if float(cur["close"]) <= float(prev["close"]):
            return None
        gap = float(prev["low"]) - float(cur["open"])
        rng = float(cur["high"]) - float(cur["low"])
        strength = min(1.0, gap / rng) if rng > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=max(0.0, strength),
            confidence=0.75,
            evidence={
                "gap": gap,
                "prev_close": float(prev["close"]),
                "cur_close": float(cur["close"]),
            },
        )
