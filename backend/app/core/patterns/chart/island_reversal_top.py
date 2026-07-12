"""Island reversal top — gap up + cluster of bars + gap down at uptrend top."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import volume_climax_at_point


class IslandReversalTopPattern:
    pattern_id: str = "island_reversal_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        volumes = win["volume"].to_numpy(dtype=float)
        # Find a gap-up bar followed by an island then a gap-down bar
        opens = win["open"].to_numpy(dtype=float)
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        # Gap-down at the very last bar
        if opens[-1] >= lows[-2]:
            return None
        # Search backward for a matching gap-up that ends an "island"
        for k in range(len(win) - 5, 1, -1):
            if opens[k] > highs[k - 1] * 1.005:  # gap up at k
                island_lows = lows[k:-1]
                if island_lows.size == 0:
                    continue
                # Island lows must all be above prior bar's high (the gap)
                if (island_lows > highs[k - 1]).all():
                    vol_climax = volume_climax_at_point(volumes, idx=len(volumes)-1)
                    confidence = 0.72 if vol_climax else 0.50
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="SHORT",
                        strength=0.7,
                        confidence=confidence,
                        evidence={
                            "gap_up_idx": int(k),
                            "gap_down_idx": int(len(win) - 1),
                            "island_size": int(len(win) - 1 - k),
                            "vol_climax": vol_climax,
                        },
                    )
        return None
