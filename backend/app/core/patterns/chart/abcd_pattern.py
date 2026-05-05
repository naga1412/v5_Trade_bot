"""ABCD pattern — equal AB and CD legs separated by countertrend BC.

Direction: LONG if D is below A (bullish completion at low).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._harmonic_helpers import find_xabcd_pivots


class AbcdPatternPattern:
    pattern_id: str = "abcd_pattern"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        pivots = find_xabcd_pivots(
            bars, lookback=self.LOOKBACK, current_idx=current_idx
        )
        if pivots is None:
            return None
        # Use last 4 of the 5 pivots (skip the X pivot for ABCD)
        a, b, c, d = pivots[-4:]
        ab = abs(b[2] - a[2])
        cd = abs(d[2] - c[2])
        if ab == 0:
            return None
        # AB ≈ CD within 15%
        if abs(ab - cd) / ab > 0.15:
            return None
        if a[1] == "high" and d[1] == "low":
            direction: Direction = "LONG"
        elif a[1] == "low" and d[1] == "high":
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.65,
            confidence=0.55,
            evidence={
                "A_idx": a[0],
                "B_idx": b[0],
                "C_idx": c[0],
                "D_idx": d[0],
                "AB": ab,
                "CD": cd,
            },
        )
