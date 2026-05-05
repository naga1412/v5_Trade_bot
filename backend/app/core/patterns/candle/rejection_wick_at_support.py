"""Rejection wick at support — long lower wick within 0.5 % of recent swing low.

Symmetric to :mod:`rejection_wick_at_resistance`:
- the bar's low is within 0.5 % of the 20-bar swing low
- lower wick ≥ 2 × the bar's body

Implies a LONG bias (rejection of an attempt at support).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import (
    body_size,
    lower_wick,
    recent_swing_low,
)

_SWING_LOOKBACK = 20
_PROXIMITY = 0.005  # 0.5 %
_WICK_BODY_RATIO = 2.0


class RejectionWickAtSupportPattern:
    pattern_id: str = "rejection_wick_at_support"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _SWING_LOOKBACK or current_idx >= len(bars):
            return None
        cur = bars.iloc[current_idx]
        body = body_size(cur)
        if body <= 0:
            return None
        if lower_wick(cur) < _WICK_BODY_RATIO * body:
            return None
        prior = bars.iloc[: current_idx]
        swing_low = recent_swing_low(prior, lookback=_SWING_LOOKBACK)
        if swing_low <= 0:
            return None
        if abs(float(cur["low"]) - swing_low) / swing_low > _PROXIMITY:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=min(1.0, lower_wick(cur) / (body * _WICK_BODY_RATIO * 2.0)),
            confidence=0.75,
            evidence={
                "lower_wick": float(lower_wick(cur)),
                "body": float(body),
                "swing_low": float(swing_low),
                "cur_low": float(cur["low"]),
            },
        )
