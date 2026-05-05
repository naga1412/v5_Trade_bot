"""Volume climax top — volume spike (≥ 3× avg) at swing high with bearish close."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class VolumeClimaxTopPattern:
    pattern_id: str = "volume_climax_top"
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
        # Swing high context: current high is the max of the lookback window
        if float(cur["high"]) < float(win["high"].iloc[:-1].max()):
            return None
        # Bearish close
        if float(cur["close"]) >= float(cur["open"]):
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.75,
            confidence=0.65,
            evidence={
                "vol_ratio": cur_vol / avg_vol,
                "current_high": float(cur["high"]),
                "current_close": float(cur["close"]),
            },
        )
