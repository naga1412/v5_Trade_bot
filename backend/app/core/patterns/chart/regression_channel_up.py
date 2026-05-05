"""Regression channel up — linear regression with positive slope, ±2σ envelope."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import fit_trend_line


class RegressionChannelUpPattern:
    pattern_id: str = "regression_channel_up"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        closes = win["close"].to_numpy(dtype=float)
        n = len(win)
        xs = np.arange(n, dtype=float)
        slope, intercept = fit_trend_line(xs, closes)
        if slope <= 0:
            return None
        fit = slope * xs + intercept
        resid = closes - fit
        sigma = float(resid.std())
        scale = float(closes.max() - closes.min())
        if sigma < scale * 0.001 or scale == 0:
            return None
        # Material slope: total range > 2σ
        if slope * n < 2 * sigma:
            return None
        # ≥ 90% of bars within ±2σ
        within = (np.abs(resid) < 2 * sigma).sum() / n
        if within < 0.9:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "slope": float(slope),
                "intercept": float(intercept),
                "sigma": sigma,
            },
        )
