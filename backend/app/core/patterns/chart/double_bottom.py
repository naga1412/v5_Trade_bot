"""Double bottom — bullish reversal: two troughs ~equal depth with a peak between."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_lows


class DoubleBottomPattern:
    pattern_id: str = "double_bottom"
    pattern_type: PatternType = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        volumes = win["volume"].to_numpy(dtype=float)
        prom = max(lows.std() * 0.3, 0.01)
        troughs = find_swing_lows(lows, prominence=prom, distance=5)
        if len(troughs) < 2:
            return None
        troughs_sorted = sorted(troughs, key=lambda i: lows[i])[:5]
        for i, t1 in enumerate(troughs_sorted):
            for t2 in troughs_sorted[i + 1 :]:
                if abs(t1 - t2) < 5:
                    continue
                a, b = sorted((t1, t2))
                l1, l2 = float(lows[a]), float(lows[b])
                if abs(l1 - l2) / max(l1, l2) > 0.01:
                    continue
                peak = float(highs[a:b].max()) if b > a else float(highs[a])
                close = float(win["close"].iloc[-1])
                if close <= peak:
                    continue
                # Volume divergence: second trough quieter = accumulation, not panic.
                v1, v2 = float(volumes[a]), float(volumes[b])
                vol_divergence = v2 < v1 * 0.85 if v1 > 0 else False
                # High confidence when volume confirms accumulation; moderate otherwise.
                confidence = 0.80 if vol_divergence else 0.55
                return PatternFire(
                    pattern_id=self.pattern_id,
                    direction="LONG",
                    strength=0.7,
                    confidence=confidence,
                    evidence={
                        "trough1_idx": int(a),
                        "trough2_idx": int(b),
                        "trough1_low": l1,
                        "trough2_low": l2,
                        "peak": peak,
                        "vol_t1": round(v1, 2),
                        "vol_t2": round(v2, 2),
                        "vol_divergence": vol_divergence,
                    },
                )
        return None
