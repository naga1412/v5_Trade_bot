"""Strict three white soldiers — three bullish bars with strict body/wick ratios.

Stricter than TA-Lib's CDL3WHITESOLDIERS:
- each body ≥ 0.7 × ATR(14)
- max wick (upper or lower) ≤ 25 % of that bar's body
- each close strictly higher than the previous
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import (
    body_size,
    compute_atr,
    is_bullish,
    lower_wick,
    upper_wick,
)


class StrictThreeWhiteSoldiersPattern:
    pattern_id: str = "strict_three_white_soldiers"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 16 or current_idx >= len(bars):
            return None
        atr = compute_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        last3 = bars.iloc[current_idx - 2 : current_idx + 1]
        if not all(is_bullish(b) for _, b in last3.iterrows()):
            return None
        for _, b in last3.iterrows():
            body = body_size(b)
            if body < 0.7 * atr:
                return None
            wick_max = max(upper_wick(b), lower_wick(b))
            if wick_max > 0.25 * body:
                return None
        closes = last3["close"].to_numpy(dtype=float)
        if not (closes[1] > closes[0] and closes[2] > closes[1]):
            return None
        third_body = body_size(last3.iloc[-1])
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=min(1.0, third_body / atr / 2.0),
            confidence=0.8,
            evidence={"atr": float(atr), "third_body": float(third_body)},
        )
