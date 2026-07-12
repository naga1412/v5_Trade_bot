"""Saucer top — long, shallow concave-down top."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_contracts_second_half


class SaucerTopPattern:
    pattern_id: str = "saucer_top"
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
        a, b, c = np.polyfit(xs, highs, 2)
        apex_x = -b / (2 * a) if a != 0 else -1
        if a >= -1e-6 or apex_x < 0 or apex_x > len(highs):
            return None
        price_range = float(highs.max() - highs.min())
        depth_ratio = price_range / float(highs.mean())
        if depth_ratio < 0.005 or depth_ratio > 0.3:
            return None
        fit = a * xs**2 + b * xs + c
        ss_res = float(np.sum((highs - fit) ** 2))
        ss_tot = float(np.sum((highs - highs.mean()) ** 2)) + 1e-9
        r2 = 1.0 - ss_res / ss_tot
        if r2 < 0.6:
            return None
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.55,
            confidence=confidence,
            evidence={
                "curvature_a": float(a),
                "r2": r2,
                "apex_x": float(apex_x),
                "vol_contracting": vol_contracting,
            },
        )
