"""Key reversal long — new low followed by close above prior high.

Two gates:
- current bar's low < prior bar's low (new short-term low)
- current bar's close > prior bar's high (closes above the rejected level)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class KeyReversalLongPattern:
    pattern_id: str = "key_reversal_long"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (
            float(cur["low"]) < float(prev["low"])
            and float(cur["close"]) > float(prev["high"])
        ):
            return None
        rng = float(cur["high"]) - float(cur["low"])
        body = abs(float(cur["close"]) - float(cur["open"]))
        strength = min(1.0, body / rng) if rng > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=max(0.0, strength),
            confidence=0.8,
            evidence={
                "prev_low": float(prev["low"]),
                "prev_high": float(prev["high"]),
                "cur_low": float(cur["low"]),
                "cur_close": float(cur["close"]),
            },
        )
