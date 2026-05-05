"""Outside-bar bearish reversal in overbought (RSI > 70) context.

Symmetric to :mod:`outside_bar_reversal_long`:
- current bar's high > prior bar's high
- current bar's low < prior bar's low
- current bar closes bearish (close < open)
- RSI(14) at the prior bar is > 70 (overbought context)
"""
from __future__ import annotations

import math

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import compute_rsi_at, is_bearish

_OVERBOUGHT = 70.0


class OutsideBarReversalShortPattern:
    pattern_id: str = "outside_bar_reversal_short"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 15 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (
            float(cur["high"]) > float(prev["high"])
            and float(cur["low"]) < float(prev["low"])
        ):
            return None
        if not is_bearish(cur):
            return None
        rsi_prev = compute_rsi_at(bars, current_idx - 1, period=14)
        if math.isnan(rsi_prev) or rsi_prev <= _OVERBOUGHT:
            return None
        body = abs(float(cur["close"]) - float(cur["open"]))
        rng = float(cur["high"]) - float(cur["low"])
        strength = min(1.0, body / rng) if rng > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=max(0.0, strength),
            confidence=0.75,
            evidence={
                "rsi_prev": float(rsi_prev),
                "outside_range": rng,
            },
        )
