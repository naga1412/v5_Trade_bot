"""Inside-bar breakout short — symmetric counterpart to inside_bar_breakout_long.

- Bar A (index ``current_idx-2``): the parent / wide-range bar.
- Bar B (index ``current_idx-1``): an inside bar (high ≤ A.high, low ≥ A.low).
- Bar C (index ``current_idx``): closes strictly below A.low → bearish breakdown.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class InsideBarBreakoutShortPattern:
    pattern_id: str = "inside_bar_breakout_short"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 2 or current_idx >= len(bars):
            return None
        a = bars.iloc[current_idx - 2]
        b = bars.iloc[current_idx - 1]
        c = bars.iloc[current_idx]
        if not (float(b["high"]) <= float(a["high"]) and float(b["low"]) >= float(a["low"])):
            return None
        if float(c["close"]) >= float(a["low"]):
            return None
        a_range = float(a["high"]) - float(a["low"])
        breakdown = float(a["low"]) - float(c["close"])
        strength = min(1.0, breakdown / a_range) if a_range > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=max(0.0, strength),
            confidence=0.7,
            evidence={
                "parent_low": float(a["low"]),
                "breakdown_close": float(c["close"]),
            },
        )
