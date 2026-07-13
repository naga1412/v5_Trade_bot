"""Island reversal bottom — gap down + cluster + gap up at downtrend bottom."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_climax_at_point


class IslandReversalBottomPattern:
    pattern_id: str = "island_reversal_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
        opens = win["open"].to_numpy(dtype=float)
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        if opens[-1] <= highs[-2]:
            return None
        for k in range(len(win) - 5, 1, -1):
            if opens[k] < lows[k - 1] * 0.995:
                island_highs = highs[k:-1]
                if island_highs.size == 0:
                    continue
                if (island_highs < lows[k - 1]).all():
                    vol_climax = volume_climax_at_point(volumes, idx=len(volumes)-1)
                    confidence = 0.72 if vol_climax else 0.50
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="LONG",
                        strength=0.7,
                        confidence=confidence,
                        evidence={
                            "gap_down_idx": int(k),
                            "gap_up_idx": int(len(win) - 1),
                            "island_size": int(len(win) - 1 - k),
                            "vol_climax": vol_climax,
                        },
                    )
        return None
