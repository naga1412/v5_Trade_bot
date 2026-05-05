"""Bump and run bottom — trend line steepens down (bump) then breaks up (run)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import fit_trend_line


class BumpAndRunBottomPattern:
    pattern_id = "bump_and_run_bottom"
    pattern_type = "chart"
    LOOKBACK = 100

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        lows = win["low"].to_numpy(dtype=float)
        n = len(win)
        xs1 = np.arange(n // 2, dtype=float)
        ys1 = lows[: n // 2]
        s1, _ = fit_trend_line(xs1, ys1)
        xs2 = np.arange(n // 2, dtype=float)
        ys2 = lows[n // 2 : 2 * (n // 2)]
        s2, _ = fit_trend_line(xs2, ys2)
        if s1 >= 0 or s2 > 2 * s1:
            return None
        bump_low = float(ys2.min())
        cur_close = float(win["close"].iloc[-1])
        if cur_close <= bump_low * 1.05:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.5,
            evidence={
                "lead_slope": s1,
                "bump_slope": s2,
                "bump_low": bump_low,
                "current_close": cur_close,
            },
        )
