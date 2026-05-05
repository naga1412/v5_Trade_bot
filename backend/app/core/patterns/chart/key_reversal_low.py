"""Key reversal low — new 20-bar low then close above prior bar's high."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire


class KeyReversalLowPattern:
    pattern_id = "key_reversal_low"
    pattern_type = "chart"
    LOOKBACK = 20

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        cur = bars.iloc[current_idx]
        prior = bars.iloc[current_idx - 1]
        prev_window_low = float(
            bars["low"].iloc[current_idx - self.LOOKBACK : current_idx].min()
        )
        if float(cur["low"]) >= prev_window_low:
            return None
        if float(cur["close"]) <= float(prior["high"]):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.65,
            evidence={
                "current_low": float(cur["low"]),
                "prev_window_low": prev_window_low,
                "close": float(cur["close"]),
                "prior_high": float(prior["high"]),
            },
        )
