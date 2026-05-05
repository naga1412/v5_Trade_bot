"""Pinbar long — long lower wick, body in upper 25% of the range, not at trend top.

Geometry gates:
- body sits in the upper 25 % of the bar's high–low range
- lower wick ≥ 2.5 × body
- bar's high < the 20-bar swing high (not exhausting an existing rally)

The "not at trend top" gate guards against firing a LONG pinbar that is also
the bar making a fresh 20-bar high — interpreted here as the swing-high check.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import (
    body_size,
    lower_wick,
    recent_swing_high,
)

_BODY_TOP_FRACTION = 0.25
_WICK_BODY_RATIO = 2.5
_SWING_LOOKBACK = 20


class PinbarLongPattern:
    pattern_id: str = "pinbar_long"
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
        # Body must sit in the upper 25 % of the bar's range.
        body_bot = min(float(cur["open"]), float(cur["close"]))
        if body_bot < high - _BODY_TOP_FRACTION * rng:
            return None
        if lower_wick(cur) < _WICK_BODY_RATIO * body:
            return None
        # Don't fire if this bar is also the 20-bar swing top.
        sub = bars.iloc[: current_idx + 1]
        swing_high = recent_swing_high(sub, lookback=_SWING_LOOKBACK)
        if high >= swing_high:
            return None
        strength = min(1.0, lower_wick(cur) / (body * _WICK_BODY_RATIO * 2.0))
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=max(0.0, strength),
            confidence=0.75,
            evidence={
                "lower_wick": float(lower_wick(cur)),
                "body": float(body),
                "swing_high": float(swing_high),
            },
        )
