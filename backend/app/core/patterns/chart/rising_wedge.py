"""Rising wedge — both lines rising but support steeper than resistance (bearish)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import (
    find_swing_highs,
    find_swing_lows,
    volume_contracts_second_half,
)


class RisingWedgePattern:
    pattern_id: str = "rising_wedge"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        prom_h = max(float(highs.std()) * 0.15, 0.5)
        prom_l = max(float(lows.std()) * 0.15, 0.5)
        peaks = find_swing_highs(highs, prominence=prom_h, distance=4)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=4)
        if len(peaks) < 2 or len(troughs) < 2:
            return None
        s_h, _ = np.polyfit(np.array(peaks, dtype=float), highs[peaks], 1)
        s_l, _ = np.polyfit(np.array(troughs, dtype=float), lows[troughs], 1)
        # Both rising, support steeper than resistance
        if s_h <= 0 or s_l <= 0 or s_l <= s_h:
            return None
        volumes = win["volume"].to_numpy(dtype=float)
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=confidence,
            evidence={
                "high_slope": float(s_h),
                "low_slope": float(s_l),
                "vol_contracting": vol_contracting,
            },
        )
