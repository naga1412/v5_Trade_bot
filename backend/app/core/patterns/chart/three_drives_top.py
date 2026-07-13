"""Three drives top — three consecutive higher peaks (each ~1.27× of prior swing)."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, volume_diverges_at_points


class ThreeDrivesTopPattern:
    pattern_id: str = "three_drives_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        volumes = win["volume"].to_numpy(dtype=float)
        prom = max(float(highs.std()) * 0.2, 0.5)
        peaks = find_swing_highs(highs, prominence=prom, distance=4)
        if len(peaks) < 3:
            return None
        last3 = peaks[-3:]
        h = [float(highs[i]) for i in last3]
        # Each higher than prior
        if not (h[0] < h[1] < h[2]):
            return None
        vol_divergence = volume_diverges_at_points(volumes, idx1=last3[0], idx2=last3[2])
        confidence = 0.78 if vol_divergence else 0.52
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=confidence,
            evidence={
                "peak_indices": [int(p) for p in last3],
                "peak_heights": h,
                "vol_divergence": vol_divergence,
            },
        )
