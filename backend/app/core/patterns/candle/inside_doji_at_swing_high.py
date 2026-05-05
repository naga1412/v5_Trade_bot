"""Inside-doji at swing high — doji that is an inside-bar of a recent swing-high parent.

Symmetric to :mod:`inside_doji_at_swing_low`:
- current bar is an inside-bar of the immediate prior bar
- inside-bar must show real range shrinkage (parent strictly wider)
- current bar is a doji: body ≤ 10 % of its high–low range
- prior bar's high sits within 0.5 % of the 20-bar swing high

SHORT bias — read as a coil at resistance that is likely to release downward.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import body_size, recent_swing_high

_SWING_LOOKBACK = 20
_PROXIMITY = 0.005  # 0.5 %
_DOJI_BODY_FRACTION = 0.1


class InsideDojiAtSwingHighPattern:
    pattern_id: str = "inside_doji_at_swing_high"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _SWING_LOOKBACK or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (
            float(cur["high"]) <= float(prev["high"])
            and float(cur["low"]) >= float(prev["low"])
        ):
            return None
        prev_range = float(prev["high"]) - float(prev["low"])
        rng = float(cur["high"]) - float(cur["low"])
        if rng <= 0 or prev_range <= 0 or prev_range < 2.0 * rng:
            return None
        if body_size(cur) > _DOJI_BODY_FRACTION * rng:
            return None
        prior = bars.iloc[: current_idx]
        swing_high = recent_swing_high(prior, lookback=_SWING_LOOKBACK)
        if swing_high <= 0:
            return None
        if abs(float(prev["high"]) - swing_high) / swing_high > _PROXIMITY:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=min(1.0, 1.0 - body_size(cur) / (_DOJI_BODY_FRACTION * rng)),
            confidence=0.7,
            evidence={
                "swing_high": float(swing_high),
                "prev_high": float(prev["high"]),
                "doji_range": float(rng),
            },
        )
