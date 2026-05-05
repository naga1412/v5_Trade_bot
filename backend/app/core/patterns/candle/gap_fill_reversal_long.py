"""Gap-fill reversal "long" — gap up followed by close below the gap level → SHORT.

Naming oddity per the SP-2 plan: the suffix ``_long`` describes the gap
*direction* (gap up, the "long" side), not the trade direction. The detector
fires SHORT because a filled gap-up rejects the breakout.

Gates:
- prior bar exists
- current bar opens strictly above prior bar's high (gap up)
- current bar closes below prior bar's close (gap "filled" downward)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class GapFillReversalLongPattern:
    pattern_id: str = "gap_fill_reversal_long"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if float(cur["open"]) <= float(prev["high"]):
            return None
        if float(cur["close"]) >= float(prev["close"]):
            return None
        gap = float(cur["open"]) - float(prev["high"])
        rng = float(cur["high"]) - float(cur["low"])
        strength = min(1.0, gap / rng) if rng > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=max(0.0, strength),
            confidence=0.75,
            evidence={
                "gap": gap,
                "prev_close": float(prev["close"]),
                "cur_close": float(cur["close"]),
            },
        )
