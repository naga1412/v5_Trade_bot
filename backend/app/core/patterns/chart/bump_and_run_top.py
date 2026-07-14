"""Bump and run top — trend line steepens (bump) then breaks down (run)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import fit_trend_line, volume_contracts_second_half


class BumpAndRunTopPattern:
    pattern_id: str = "bump_and_run_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 100

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
        highs = win["high"].to_numpy(dtype=float)
        n = len(win)
        # Lead-in: first half slope; bump: second half slope (must be ≥ 2x)
        xs1 = np.arange(n // 2, dtype=float)
        ys1 = highs[: n // 2]
        s1, _ = fit_trend_line(xs1, ys1)
        xs2 = np.arange(n // 2, dtype=float)
        ys2 = highs[n // 2 : 2 * (n // 2)]
        s2, _ = fit_trend_line(xs2, ys2)
        if s1 <= 0 or s2 < 2 * s1:
            return None
        # Bump high vs current close — current must have broken down from bump high
        bump_high = float(ys2.max())
        cur_close = float(win["close"].iloc[-1])
        # Threshold: current close must be at least 5% below bump_high
        if cur_close >= bump_high * 0.95:
            return None
        vol_contracting = volume_contracts_second_half(volumes)
        confidence = 0.68 if vol_contracting else 0.48
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=confidence,
            evidence={
                "lead_slope": s1,
                "bump_slope": s2,
                "bump_high": bump_high,
                "current_close": cur_close,
                "vol_contracting": vol_contracting,
            },
        )
