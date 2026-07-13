"""V top — sharp uptrend immediately reversed by sharp downtrend at the apex."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr, volume_climax_at_point


class VTopPattern:
    pattern_id: str = "v_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        peak_idx = int(np.argmax(highs))
        n = len(win)
        # Apex must be in the middle third
        if peak_idx < n // 3 or peak_idx > 2 * n // 3:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        # Slope before peak (rise) and after peak (fall) measured per bar
        before = highs[: peak_idx + 1]
        after = highs[peak_idx:]
        rise = (before[-1] - before[0]) / max(len(before) - 1, 1)
        fall = (after[-1] - after[0]) / max(len(after) - 1, 1)
        if rise < 0.5 * atr or fall > -0.5 * atr:
            return None
        volumes = win["volume"].to_numpy(dtype=float)
        vol_climax = volume_climax_at_point(volumes, idx=peak_idx)
        confidence = 0.68 if vol_climax else 0.45
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=confidence,
            evidence={
                "peak_idx": peak_idx,
                "peak_high": float(highs[peak_idx]),
                "rise_per_bar": float(rise),
                "fall_per_bar": float(fall),
                "atr": atr,
                "vol_climax": vol_climax,
            },
        )
