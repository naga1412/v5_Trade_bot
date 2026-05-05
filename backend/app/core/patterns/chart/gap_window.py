"""Gap window — any unfilled gap that persists for ≥ 5 bars (trend continuation)."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class GapWindowPattern:
    pattern_id = "gap_window"
    pattern_type = "chart"
    LOOKBACK = 30

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        # Look for a gap in the last 10 bars that hasn't been filled by current bar
        for k in range(current_idx - 10, current_idx):
            if k < 1:
                continue
            gap_open = float(bars["open"].iloc[k])
            prev_high = float(bars["high"].iloc[k - 1])
            prev_low = float(bars["low"].iloc[k - 1])
            if gap_open > prev_high + 0.5 * atr:
                # Gap up; unfilled if all subsequent lows stay above prev_high
                subseq_lows = bars["low"].iloc[k : current_idx + 1].to_numpy(dtype=float)
                if (subseq_lows > prev_high).all():
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="LONG",
                        strength=0.6,
                        confidence=0.5,
                        evidence={
                            "gap_idx": int(k),
                            "gap_size": gap_open - prev_high,
                            "atr": atr,
                        },
                    )
            elif gap_open < prev_low - 0.5 * atr:
                subseq_highs = (
                    bars["high"].iloc[k : current_idx + 1].to_numpy(dtype=float)
                )
                if (subseq_highs < prev_low).all():
                    return PatternFire(
                        pattern_id=self.pattern_id,
                        direction="SHORT",
                        strength=0.6,
                        confidence=0.5,
                        evidence={
                            "gap_idx": int(k),
                            "gap_size": prev_low - gap_open,
                            "atr": atr,
                        },
                    )
        return None
