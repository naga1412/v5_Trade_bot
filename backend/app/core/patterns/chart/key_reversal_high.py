"""Key reversal high — new 20-bar high then close below prior bar's low."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire


class KeyReversalHighPattern:
    pattern_id = "key_reversal_high"
    pattern_type = "chart"
    LOOKBACK = 20

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        cur = bars.iloc[current_idx]
        prior = bars.iloc[current_idx - 1]
        prev_window_high = float(
            bars["high"].iloc[current_idx - self.LOOKBACK : current_idx].max()
        )
        if float(cur["high"]) <= prev_window_high:
            return None
        if float(cur["close"]) >= float(prior["low"]):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=0.65,
            evidence={
                "current_high": float(cur["high"]),
                "prev_window_high": prev_window_high,
                "close": float(cur["close"]),
                "prior_low": float(prior["low"]),
            },
        )
