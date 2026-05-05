"""Harmonic Crab — XABCD with D = 1.618 × XA extension."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._harmonic_helpers import (
    find_xabcd_pivots,
    ratio_in_band,
    safe_ratio,
)


class HarmonicCrabPattern:
    pattern_id: str = "harmonic_crab"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        pivots = find_xabcd_pivots(
            bars, lookback=self.LOOKBACK, current_idx=current_idx
        )
        if pivots is None:
            return None
        x, a, b, c, d = pivots
        xa = abs(a[2] - x[2])
        ab = abs(b[2] - a[2])
        xd = abs(d[2] - x[2])
        if xa == 0:
            return None
        ab_ratio = safe_ratio(ab, xa)
        if not (
            ratio_in_band(ab_ratio, 0.382, tol=0.1)
            or ratio_in_band(ab_ratio, 0.618, tol=0.1)
        ):
            return None
        if not ratio_in_band(safe_ratio(xd, xa), 1.618, tol=0.15):
            return None
        if x[1] == "high" and d[1] == "low":
            direction: Direction = "LONG"
        elif x[1] == "low" and d[1] == "high":
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.65,
            confidence=0.5,
            evidence={
                "XA": xa,
                "XD": xd,
                "AB": ab,
                "C_idx": c[0],
            },
        )
