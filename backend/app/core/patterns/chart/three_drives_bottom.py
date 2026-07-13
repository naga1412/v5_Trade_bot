"""Three drives bottom — three consecutive lower troughs."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_lows, volume_diverges_at_points


class ThreeDrivesBottomPattern:
    pattern_id: str = "three_drives_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        lows = win["low"].to_numpy(dtype=float)
        volumes = win["volume"].to_numpy(dtype=float)
        prom = max(float(lows.std()) * 0.2, 0.5)
        troughs = find_swing_lows(lows, prominence=prom, distance=4)
        if len(troughs) < 3:
            return None
        last3 = troughs[-3:]
        lvals = [float(lows[i]) for i in last3]
        if not (lvals[0] > lvals[1] > lvals[2]):
            return None
        vol_divergence = volume_diverges_at_points(volumes, idx1=last3[0], idx2=last3[2])
        confidence = 0.78 if vol_divergence else 0.52
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=confidence,
            evidence={
                "trough_indices": [int(t) for t in last3],
                "trough_lows": lvals,
                "vol_divergence": vol_divergence,
            },
        )
