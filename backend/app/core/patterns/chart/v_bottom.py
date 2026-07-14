"""V bottom — sharp downtrend immediately reversed by sharp uptrend."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr, volume_climax_at_point


class VBottomPattern:
    pattern_id: str = "v_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        lows = win["low"].to_numpy(dtype=float)
        trough_idx = int(np.argmin(lows))
        n = len(win)
        if trough_idx < n // 3 or trough_idx > 2 * n // 3:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        before = lows[: trough_idx + 1]
        after = lows[trough_idx:]
        fall = (before[-1] - before[0]) / max(len(before) - 1, 1)
        rise = (after[-1] - after[0]) / max(len(after) - 1, 1)
        if fall > -0.5 * atr or rise < 0.5 * atr:
            return None
        volumes = win["volume"].to_numpy(dtype=float)
        vol_climax = volume_climax_at_point(volumes, idx=trough_idx)
        confidence = 0.68 if vol_climax else 0.45
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=confidence,
            evidence={
                "trough_idx": trough_idx,
                "trough_low": float(lows[trough_idx]),
                "fall_per_bar": float(fall),
                "rise_per_bar": float(rise),
                "atr": atr,
                "vol_climax": vol_climax,
            },
        )
