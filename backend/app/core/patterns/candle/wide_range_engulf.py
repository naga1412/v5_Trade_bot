"""Wide-range engulfing — TA-Lib-style engulfing with body and volume confirmation.

Adds two strict gates over the basic engulfing pattern:
- current bar's body ≥ 1.5 × the prior bar's body
- current bar's volume ≥ 1.5 × the trailing 20-bar mean volume

Direction follows the engulfing colour (bullish current → LONG, bearish → SHORT).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.candle._helpers import body_size, is_bearish, is_bullish

_BODY_RATIO = 1.5
_VOLUME_RATIO = 1.5
_VOL_LOOKBACK = 20


class WideRangeEngulfPattern:
    pattern_id: str = "wide_range_engulf"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _VOL_LOOKBACK or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]

        # Engulfing geometry: current body must straddle the prior body.
        prev_top = max(float(prev["open"]), float(prev["close"]))
        prev_bot = min(float(prev["open"]), float(prev["close"]))
        cur_top = max(float(cur["open"]), float(cur["close"]))
        cur_bot = min(float(cur["open"]), float(cur["close"]))
        if not (cur_top >= prev_top and cur_bot <= prev_bot):
            return None

        # Direction must be opposite the prior bar.
        direction: Direction
        if is_bullish(cur) and is_bearish(prev):
            direction = "LONG"
        elif is_bearish(cur) and is_bullish(prev):
            direction = "SHORT"
        else:
            return None

        prev_body = body_size(prev)
        cur_body = body_size(cur)
        if prev_body <= 0 or cur_body < _BODY_RATIO * prev_body:
            return None

        vol_window = bars["volume"].iloc[
            current_idx - _VOL_LOOKBACK : current_idx
        ]
        avg_vol = float(vol_window.mean())
        cur_vol = float(cur["volume"])
        if avg_vol <= 0 or cur_vol < _VOLUME_RATIO * avg_vol:
            return None

        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=min(1.0, cur_body / (prev_body * _BODY_RATIO * 2.0)),
            confidence=0.8,
            evidence={
                "body_ratio": cur_body / prev_body,
                "volume_ratio": cur_vol / avg_vol,
            },
        )
