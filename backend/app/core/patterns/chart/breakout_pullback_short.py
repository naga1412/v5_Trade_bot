"""Breakout pullback short — break below support, then pullback to it, then drop."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire
from app.core.patterns.chart._helpers import recent_atr


class BreakoutPullbackShortPattern:
    pattern_id = "breakout_pullback_short"
    pattern_type = "chart"
    LOOKBACK = 50

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0:
            return None
        first_half = win.iloc[: len(win) // 2]
        support = float(first_half["low"].min())
        second_half = win.iloc[len(win) // 2 :]
        if float(second_half["close"].min()) > support - 0.5 * atr:
            return None
        cur_close = float(win["close"].iloc[-1])
        cur_high = float(win["high"].iloc[-1])
        if not (support - 0.5 * atr <= cur_high <= support + 0.5 * atr):
            return None
        if cur_close >= support:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=0.7,
            confidence=0.6,
            evidence={
                "support": support,
                "current_high": cur_high,
                "current_close": cur_close,
                "atr": atr,
            },
        )
