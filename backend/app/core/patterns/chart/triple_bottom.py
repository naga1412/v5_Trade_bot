"""Triple bottom — bullish reversal: three troughs within 1.5% of each other."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_lows, volume_diverges_at_points


class TripleBottomPattern:
    pattern_id: str = "triple_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        lows = win["low"].to_numpy(dtype=float)
        volumes = win["volume"].to_numpy(dtype=float)
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
                    vol_divergence = volume_diverges_at_points(volumes, idx1=a, idx2=c)
                    confidence = 0.80 if vol_divergence else 0.55
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="LONG",
                        strength=0.75,
                        confidence=confidence,
                        evidence={
                            "trough_indices": [int(a), int(b), int(c)],
                            "trough_lows": lvals,
                            "vol_t1": round(float(volumes[a]), 2),
                            "vol_t3": round(float(volumes[c]), 2),
                            "vol_divergence": vol_divergence,
                        },
                    )
        return None
