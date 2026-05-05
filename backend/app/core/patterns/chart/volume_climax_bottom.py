"""Volume climax bottom — volume spike at swing low with bullish close."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class VolumeClimaxBottomPattern:
    pattern_id: str = "volume_climax_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 50

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        cur = bars.iloc[current_idx]
        avg_vol = float(win["volume"].iloc[:-1].mean())
        cur_vol = float(cur["volume"])
        if avg_vol == 0 or cur_vol < 3 * avg_vol:
            return None
        if float(cur["low"]) > float(win["low"].iloc[:-1].min()):
            return None
        if float(cur["close"]) <= float(cur["open"]):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.75,
            confidence=0.65,
            evidence={
                "vol_ratio": cur_vol / avg_vol,
                "current_low": float(cur["low"]),
                "current_close": float(cur["close"]),
            },
        )
