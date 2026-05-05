"""Inside-bar breakout long — inside bar at index N-1, breakout close at index N.

Three-bar formation:
- Bar A (index ``current_idx-2``): the parent / wide-range bar.
- Bar B (index ``current_idx-1``): an inside bar (high ≤ A.high, low ≥ A.low).
- Bar C (index ``current_idx``): closes strictly above A.high → bullish breakout.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class InsideBarBreakoutLongPattern:
    pattern_id: str = "inside_bar_breakout_long"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 2 or current_idx >= len(bars):
            return None
        a = bars.iloc[current_idx - 2]
        b = bars.iloc[current_idx - 1]
        c = bars.iloc[current_idx]
        # Bar B is an inside bar of A.
        if not (float(b["high"]) <= float(a["high"]) and float(b["low"]) >= float(a["low"])):
            return None
        # Bar C closes above A.high.
        if float(c["close"]) <= float(a["high"]):
            return None
        # Strength = distance above A.high normalised by A's range.
        a_range = float(a["high"]) - float(a["low"])
        breakout = float(c["close"]) - float(a["high"])
        strength = min(1.0, breakout / a_range) if a_range > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=max(0.0, strength),
            confidence=0.7,
            evidence={
                "parent_high": float(a["high"]),
                "breakout_close": float(c["close"]),
            },
        )
