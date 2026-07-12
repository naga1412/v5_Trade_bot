"""Broadening bottom — diverging trendlines at bottom of range."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows, volume_contracts_second_half


class BroadeningBottomPattern:
    pattern_id: str = "broadening_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
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
        if s_h <= 0 or s_l >= 0:
            return None
        cur_close = float(win["close"].iloc[-1])
        win_mid = (float(highs.max()) + float(lows.min())) / 2
        if cur_close > win_mid * 1.02:
            return None
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.65,
            confidence=confidence,
            evidence={
                "high_slope": float(s_h),
                "low_slope": float(s_l),
                "vol_contracting": vol_contracting,
            },
        )
