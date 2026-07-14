"""Inverse head and shoulders — bullish reversal: three troughs, middle deepest."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_lows, volume_diverges_at_points


class InverseHeadAndShouldersPattern:
    pattern_id: str = "inverse_head_and_shoulders"
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
        for i in range(len(troughs) - 2):
            ls, head, rs = troughs[i], troughs[i + 1], troughs[i + 2]
            ls_l = float(lows[ls])
            head_l = float(lows[head])
            rs_l = float(lows[rs])
            if head_l >= ls_l or head_l >= rs_l:
                continue
            if abs(ls_l - rs_l) / max(ls_l, rs_l) > 0.02:
                continue
            # Right shoulder quieter than left — selling exhaustion on the re-test.
            vol_divergence = volume_diverges_at_points(volumes, idx1=ls, idx2=rs)
            confidence = 0.82 if vol_divergence else 0.58
            return PatternFire(
                pattern_id=self.pattern_id,
                direction="LONG",
                strength=0.8,
                confidence=confidence,
                evidence={
                    "left_shoulder_idx": int(ls),
                    "head_idx": int(head),
                    "right_shoulder_idx": int(rs),
                    "left_shoulder_low": ls_l,
                    "head_low": head_l,
                    "right_shoulder_low": rs_l,
                    "vol_ls": round(float(volumes[ls]), 2),
                    "vol_rs": round(float(volumes[rs]), 2),
                    "vol_divergence": vol_divergence,
                },
            )
        return None
