"""Pinbar short — long upper wick, body in lower 25% of the range, not at trend bottom.

Symmetric to :mod:`pinbar_long`:
- body sits in the lower 25 % of the bar's high–low range
- upper wick ≥ 2.5 × body
- bar's low > the 20-bar swing low (not exhausting an existing decline)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import (
    body_size,
    recent_swing_low,
    upper_wick,
)

_BODY_BOTTOM_FRACTION = 0.25
_WICK_BODY_RATIO = 2.5
_SWING_LOOKBACK = 20


class PinbarShortPattern:
    pattern_id: str = "pinbar_short"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _SWING_LOOKBACK or current_idx >= len(bars):
            return None
        cur = bars.iloc[current_idx]
        high = float(cur["high"])
        low = float(cur["low"])
        rng = high - low
        if rng <= 0:
            return None
        body = body_size(cur)
        if body <= 0:
            return None
        body_top = max(float(cur["open"]), float(cur["close"]))
        if body_top > low + _BODY_BOTTOM_FRACTION * rng:
            return None
        if upper_wick(cur) < _WICK_BODY_RATIO * body:
            return None
        sub = bars.iloc[: current_idx + 1]
        swing_low = recent_swing_low(sub, lookback=_SWING_LOOKBACK)
        if low <= swing_low:
            return None
        strength = min(1.0, upper_wick(cur) / (body * _WICK_BODY_RATIO * 2.0))
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=max(0.0, strength),
            confidence=0.75,
            evidence={
                "upper_wick": float(upper_wick(cur)),
                "body": float(body),
                "swing_low": float(swing_low),
            },
        )
