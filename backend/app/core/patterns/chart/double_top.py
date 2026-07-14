"""Double top — bearish reversal: two peaks ~equal height with a trough between."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, volume_diverges_at_points


class DoubleTopPattern:
    pattern_id: str = "double_top"
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
        prom = max(highs.std() * 0.3, 0.01)
        peaks = find_swing_highs(highs, prominence=prom, distance=5)
        if len(peaks) < 2:
            return None
        peaks_sorted = sorted(peaks, key=lambda i: highs[i], reverse=True)[:5]
        for i, p1 in enumerate(peaks_sorted):
            for p2 in peaks_sorted[i + 1 :]:
                if abs(p1 - p2) < 5:
                    continue
                a, b = sorted((p1, p2))
                h1, h2 = float(highs[a]), float(highs[b])
                if abs(h1 - h2) / max(h1, h2) > 0.01:
                    continue
                trough = float(lows[a:b].min()) if b > a else float(lows[a])
                close = float(win["close"].iloc[-1])
                if close >= trough:
                    continue
                vol_divergence = volume_diverges_at_points(volumes, idx1=a, idx2=b)
                confidence = 0.75 if vol_divergence else 0.50
                return PatternFire(
                    pattern_id=self.pattern_id,
                    direction="SHORT",
                    strength=0.7,
                    confidence=confidence,
                    evidence={
                        "peak1_idx": int(a),
                        "peak2_idx": int(b),
                        "peak1_high": h1,
                        "peak2_high": h2,
                        "trough": trough,
                        "vol_p1": round(float(volumes[a]), 2),
                        "vol_p2": round(float(volumes[b]), 2),
                        "vol_divergence": vol_divergence,
                    },
                )
        return None
