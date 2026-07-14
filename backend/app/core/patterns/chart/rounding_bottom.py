"""Rounding bottom — smooth concave-up curve fit on lows."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_contracts_second_half


class RoundingBottomPattern:
    pattern_id: str = "rounding_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        xs = np.arange(len(lows), dtype=float)
        a, b, c = np.polyfit(xs, lows, 2)
        apex_x = -b / (2 * a) if a != 0 else -1
        if a <= 1e-6 or apex_x < 0 or apex_x > len(lows):
            return None
        price_range = float(lows.max() - lows.min())
        if abs(a) * len(lows) ** 2 < price_range * 0.5:
            return None
        fit = a * xs**2 + b * xs + c
        ss_res = float(np.sum((lows - fit) ** 2))
        ss_tot = float(np.sum((lows - lows.mean()) ** 2)) + 1e-9
        r2 = 1.0 - ss_res / ss_tot
        if r2 < 0.7:
            return None
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.65,
            confidence=confidence,
            evidence={
                "curvature_a": float(a),
                "apex_x": float(apex_x),
                "r2": r2,
                "vol_contracting": vol_contracting,
            },
        )
