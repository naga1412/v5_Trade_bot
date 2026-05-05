"""Harmonic Gartley — XABCD pattern with 0.618 B retrace and 0.786 D retrace."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._harmonic_helpers import (
    find_xabcd_pivots,
    ratio_in_band,
    safe_ratio,
)


class HarmonicGartleyPattern:
    pattern_id = "harmonic_gartley"
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
        ad = abs(d[2] - a[2])
        # B retrace ~ 0.618 of XA; D retrace ~ 0.786 of XA
        if xa == 0:
            return None
        if not ratio_in_band(safe_ratio(ab, xa), 0.618, tol=0.1):
            return None
        if not ratio_in_band(safe_ratio(ad, xa), 0.786, tol=0.1):
            return None
        # Direction: bullish Gartley if X is high and D is low (price drops to D)
        if x[1] == "high" and d[1] == "low":
            direction = "LONG"
        elif x[1] == "low" and d[1] == "high":
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.7,
            confidence=0.55,
            evidence={
                "X_idx": x[0],
                "A_idx": a[0],
                "B_idx": b[0],
                "C_idx": c[0],
                "D_idx": d[0],
                "XA": xa,
                "AB": ab,
                "AD": ad,
            },
        )
