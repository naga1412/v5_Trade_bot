"""Rounding top — smooth concave-down arc fit on highs (parabolic fit a<0)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_contracts_second_half


class RoundingTopPattern:
    pattern_id: str = "rounding_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
        highs = win["high"].to_numpy(dtype=float)
        xs = np.arange(len(highs), dtype=float)
        a, b, _c = np.polyfit(xs, highs, 2)
        # Concave-down: a < 0; require apex inside the window
        apex_x = -b / (2 * a) if a != 0 else -1
        # Require meaningful curvature relative to price scale.
        price_range = float(highs.max() - highs.min())
        if a >= -1e-6 or apex_x < 0 or apex_x > len(highs):
            return None
        # Curvature must be material: arc must depart from a flat line meaningfully.
        if abs(a) * len(highs) ** 2 < price_range * 0.5:
            return None
        # Quality: residual variance / total variance must be small
        fit = a * xs**2 + b * xs + _c
        ss_res = float(np.sum((highs - fit) ** 2))
        ss_tot = float(np.sum((highs - highs.mean()) ** 2)) + 1e-9
        r2 = 1.0 - ss_res / ss_tot
        if r2 < 0.7:
            return None
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.65,
            confidence=confidence,
            evidence={
                "curvature_a": float(a),
                "apex_x": float(apex_x),
                "r2": r2,
                "vol_contracting": vol_contracting,
            },
        )
