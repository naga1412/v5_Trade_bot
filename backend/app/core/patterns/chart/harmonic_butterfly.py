"""Harmonic Butterfly — XABCD with D = 1.27 × XA extension."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._harmonic_helpers import (
    find_xabcd_pivots,
    ratio_in_band,
    safe_ratio,
)


class HarmonicButterflyPattern:
    pattern_id = "harmonic_butterfly"
    pattern_type = "chart"
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
        # B retrace ~ 0.786 of XA; D extension ~ 1.27 of XA
        if not ratio_in_band(safe_ratio(ab, xa), 0.786, tol=0.1):
            return None
        if not ratio_in_band(safe_ratio(xd, xa), 1.27, tol=0.15):
            return None
        if x[1] == "high" and d[1] == "low":
            direction = "LONG"
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
