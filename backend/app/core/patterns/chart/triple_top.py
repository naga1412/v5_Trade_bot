"""Triple top — bearish reversal: three peaks within 1.5% of each other."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs


class TripleTopPattern:
    pattern_id: str = "triple_top"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        prom = max(highs.std() * 0.3, 0.01)
        peaks = find_swing_highs(highs, prominence=prom, distance=5)
        if len(peaks) < 3:
            return None
        peaks_sorted = sorted(peaks, key=lambda i: highs[i], reverse=True)[:6]
        # Check all triples for ~equal heights
        n = len(peaks_sorted)
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    a, b, c = sorted((peaks_sorted[i], peaks_sorted[j], peaks_sorted[k]))
                    if (b - a) < 5 or (c - b) < 5:
                        continue
                    h = [float(highs[a]), float(highs[b]), float(highs[c])]
                    if (max(h) - min(h)) / max(h) > 0.015:
                        continue
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="SHORT",
                        strength=0.75,
                        confidence=0.65,
                        evidence={
                            "peak_indices": [int(a), int(b), int(c)],
                            "peak_heights": h,
                        },
                    )
        return None
