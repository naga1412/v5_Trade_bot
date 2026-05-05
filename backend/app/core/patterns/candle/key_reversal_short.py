"""Key reversal short — new high followed by close below prior low.

Symmetric to :mod:`key_reversal_long`:
- current bar's high > prior bar's high (new short-term high)
- current bar's close < prior bar's low (closes below the rejected level)
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class KeyReversalShortPattern:
    pattern_id: str = "key_reversal_short"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 1 or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (
            float(cur["high"]) > float(prev["high"])
            and float(cur["close"]) < float(prev["low"])
        ):
            return None
        rng = float(cur["high"]) - float(cur["low"])
        body = abs(float(cur["close"]) - float(cur["open"]))
        strength = min(1.0, body / rng) if rng > 0 else 0.5
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=max(0.0, strength),
            confidence=0.8,
            evidence={
                "prev_high": float(prev["high"]),
                "prev_low": float(prev["low"]),
                "cur_high": float(cur["high"]),
                "cur_close": float(cur["close"]),
            },
        )
