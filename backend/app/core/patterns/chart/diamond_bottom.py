"""Diamond bottom — broadening then converging at bottom of range."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_contracts_second_half


class DiamondBottomPattern:
    pattern_id: str = "diamond_bottom"
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
        lows = win["low"].to_numpy(dtype=float)
        n = len(win)
        first = n // 2
        h1, l1 = highs[:first], lows[:first]
        h2, l2 = highs[first:], lows[first:]
        xs1 = np.arange(first, dtype=float)
        xs2 = np.arange(n - first, dtype=float)
        r1, r2 = h1 - l1, h2 - l2
        sr1, _ = np.polyfit(xs1, r1, 1)
        sr2, _ = np.polyfit(xs2, r2, 1)
        scale = float(highs.max() - lows.min()) + 1e-9
        if sr1 <= scale * 0.005 or sr2 >= -scale * 0.005:
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
            strength=0.6,
            confidence=confidence,
            evidence={
                "range_slope_first": float(sr1),
                "range_slope_second": float(sr2),
                "vol_contracting": vol_contracting,
            },
        )
