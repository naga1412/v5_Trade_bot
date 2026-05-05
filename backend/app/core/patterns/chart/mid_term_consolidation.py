"""Mid-term consolidation — multi-week tight range; signal continues prior trend."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class MidTermConsolidationPattern:
    pattern_id = "mid_term_consolidation"
    pattern_type = "chart"
    LOOKBACK = 60

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        # Last 30 bars must be tight (range < 5*ATR) but earlier 30 had momentum
        recent_win = win.iloc[-30:]
        early_win = win.iloc[:30]
        recent_range = float(recent_win["high"].max() - recent_win["low"].min())
        if recent_range > 5 * atr:
            return None
        early_move = float(early_win["close"].iloc[-1] - early_win["close"].iloc[0])
        if abs(early_move) < 5 * atr:
            return None
        direction = "LONG" if early_move > 0 else "SHORT"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.6,
            confidence=0.55,
            evidence={
                "recent_range": recent_range,
                "early_move": early_move,
                "atr": atr,
            },
        )
