"""Channel up — parallel uptrend lines containing 80%+ of bars."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import fit_trend_line


class ChannelUpPattern:
    pattern_id: str = "channel_up"
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
        # Residuals envelope
        fit = slope * xs + intercept
        resid = closes - fit
        upper = float(resid.max())
        lower = float(resid.min())
        scale = float(closes.max() - closes.min()) + 1e-9
        if upper <= scale * 0.005 or lower >= -scale * 0.005:
            return None
        within = ((resid >= lower) & (resid <= upper)).sum() / n
        if within < 0.95:
            return None
        # Slope must be material — avoid flat random walks
        if slope * n < (upper - lower) * 0.3:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.65,
            confidence=0.55,
            evidence={
                "slope": float(slope),
                "channel_upper": float(upper),
                "channel_lower": float(lower),
            },
        )
