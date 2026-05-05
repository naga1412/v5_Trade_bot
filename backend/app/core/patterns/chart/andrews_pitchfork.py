"""Andrew's pitchfork — three-pivot median line projection; fire on trend direction.

Simplified: detect three significant pivots (P0=swing high, P1=swing low, P2=swing
high) and project the median line; signal in the direction of price near the median.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import find_swing_highs, find_swing_lows


class AndrewsPitchforkPattern:
    pattern_id: str = "andrews_pitchfork"
    pattern_type: PatternType = "chart"
    LOOKBACK = 80

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        prom_h = max(float(highs.std()) * 0.2, 0.5)
        prom_l = max(float(lows.std()) * 0.2, 0.5)
        peaks = find_swing_highs(highs, prominence=prom_h, distance=5)
        troughs = find_swing_lows(lows, prominence=prom_l, distance=5)
        # Need at least P0 (peak) → P1 (trough) → P2 (peak) sequence
        if len(peaks) < 2 or len(troughs) < 1:
            return None
        p0 = peaks[0]
        p2_candidates = [p for p in peaks if p > p0]
        if not p2_candidates:
            return None
        p2 = p2_candidates[0]
        p1_candidates = [t for t in troughs if p0 < t < p2]
        if not p1_candidates:
            return None
        p1 = p1_candidates[0]
        # Direction: P2 vs P0 (uptrend if higher peak, downtrend if lower)
        if highs[p2] > highs[p0]:
            direction: Direction = "LONG"
        elif highs[p2] < highs[p0]:
            direction = "SHORT"
        else:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.55,
            confidence=0.5,
            evidence={
                "p0_idx": int(p0),
                "p1_idx": int(p1),
                "p2_idx": int(p2),
                "p0_high": float(highs[p0]),
                "p2_high": float(highs[p2]),
            },
        )
