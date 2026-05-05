"""Rejection wick at resistance — long upper wick within 0.5 % of recent swing high.

Two gates:
- the bar's high is within 0.5 % of the 20-bar swing high
- upper wick ≥ 2 × the bar's body

Implies a SHORT bias (rejection of an attempt at resistance).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import (
    body_size,
    recent_swing_high,
    upper_wick,
)

_SWING_LOOKBACK = 20
_PROXIMITY = 0.005  # 0.5 %
_WICK_BODY_RATIO = 2.0


class RejectionWickAtResistancePattern:
    pattern_id: str = "rejection_wick_at_resistance"
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
        if upper_wick(cur) < _WICK_BODY_RATIO * body:
            return None
        # Use lookback EXCLUDING the current bar to define resistance.
        prior = bars.iloc[: current_idx]
        swing_high = recent_swing_high(prior, lookback=_SWING_LOOKBACK)
        if swing_high <= 0:
            return None
        # Bar's high must approach (not exceed) resistance by more than the band.
        if abs(float(cur["high"]) - swing_high) / swing_high > _PROXIMITY:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=min(1.0, upper_wick(cur) / (body * _WICK_BODY_RATIO * 2.0)),
            confidence=0.75,
            evidence={
                "upper_wick": float(upper_wick(cur)),
                "body": float(body),
                "swing_high": float(swing_high),
                "cur_high": float(cur["high"]),
            },
        )
