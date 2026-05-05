"""Triple bottom — bullish reversal: three troughs within 1.5% of each other."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import find_swing_lows


class TripleBottomPattern:
    pattern_id = "triple_bottom"
    pattern_type = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        lows = win["low"].to_numpy(dtype=float)
        prom = max(lows.std() * 0.3, 0.01)
        troughs = find_swing_lows(lows, prominence=prom, distance=5)
        if len(troughs) < 3:
            return None
        troughs_sorted = sorted(troughs, key=lambda i: lows[i])[:6]
        n = len(troughs_sorted)
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    a, b, c = sorted(
                        (troughs_sorted[i], troughs_sorted[j], troughs_sorted[k])
                    )
                    if (b - a) < 5 or (c - b) < 5:
                        continue
                    lvals = [float(lows[a]), float(lows[b]), float(lows[c])]
                    if (max(lvals) - min(lvals)) / max(lvals) > 0.015:
                        continue
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="LONG",
                        strength=0.75,
                        confidence=0.65,
                        evidence={
                            "trough_indices": [int(a), int(b), int(c)],
                            "trough_lows": lvals,
                        },
                    )
        return None
