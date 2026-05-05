"""Head and shoulders — bearish reversal: three peaks, middle highest."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs


class HeadAndShouldersPattern:
    pattern_id: str = "head_and_shoulders"
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
        # Try every consecutive triple
        for i in range(len(peaks) - 2):
            ls, head, rs = peaks[i], peaks[i + 1], peaks[i + 2]
            ls_h = float(highs[ls])
            head_h = float(highs[head])
            rs_h = float(highs[rs])
            if head_h <= ls_h or head_h <= rs_h:
                continue
            # Shoulders within 2% of each other
            if abs(ls_h - rs_h) / max(ls_h, rs_h) > 0.02:
                continue
            return PatternFire(
                pattern_id=self.pattern_id,
                direction="SHORT",
                strength=0.8,
                confidence=0.7,
                evidence={
                    "left_shoulder_idx": int(ls),
                    "head_idx": int(head),
                    "right_shoulder_idx": int(rs),
                    "left_shoulder_high": ls_h,
                    "head_high": head_h,
                    "right_shoulder_high": rs_h,
                },
            )
        return None
