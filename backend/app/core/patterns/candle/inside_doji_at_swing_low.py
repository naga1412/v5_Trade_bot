"""Inside-doji at swing low — doji that is an inside-bar of a recent swing-low parent.

Three gates:
- current bar is an inside-bar of the immediate prior bar
- current bar is a doji: body ≤ 10 % of its high–low range
- prior bar's low sits within 0.5 % of the 20-bar swing low (it IS the swing low)

LONG bias — read as a coil at support that is likely to release upward.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import body_size, recent_swing_low

_SWING_LOOKBACK = 20
_PROXIMITY = 0.005  # 0.5 %
_DOJI_BODY_FRACTION = 0.1  # body ≤ 10 % of range


class InsideDojiAtSwingLowPattern:
    pattern_id: str = "inside_doji_at_swing_low"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _SWING_LOOKBACK or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        # Inside-bar: cur high ≤ prev high and cur low ≥ prev low.
        if not (
            float(cur["high"]) <= float(prev["high"])
            and float(cur["low"]) >= float(prev["low"])
        ):
            return None
        # Inside-bar must show real range shrinkage (parent strictly wider).
        prev_range = float(prev["high"]) - float(prev["low"])
        rng = float(cur["high"]) - float(cur["low"])
        if rng <= 0 or prev_range <= 0 or prev_range < 2.0 * rng:
            return None
        # Doji: tiny body relative to range.
        if body_size(cur) > _DOJI_BODY_FRACTION * rng:
            return None
        # Prior bar must be near the swing low.
        prior = bars.iloc[: current_idx]
        swing_low = recent_swing_low(prior, lookback=_SWING_LOOKBACK)
        if swing_low <= 0:
            return None
        if abs(float(prev["low"]) - swing_low) / swing_low > _PROXIMITY:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=min(1.0, 1.0 - body_size(cur) / (_DOJI_BODY_FRACTION * rng)),
            confidence=0.7,
            evidence={
                "swing_low": float(swing_low),
                "prev_low": float(prev["low"]),
                "doji_range": float(rng),
            },
        )
