"""Breakout pullback long — break above resistance, then pullback to it, then bounce."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.chart._helpers import recent_atr


class BreakoutPullbackLongPattern:
    pattern_id: str = "breakout_pullback_long"
    pattern_type: PatternType = "chart"
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
        # First half: build resistance (mid_high)
        first_half = win.iloc[: len(win) // 2]
        resistance = float(first_half["high"].max())
        second_half = win.iloc[len(win) // 2 :]
        # Must have broken out (max close > resistance + 0.5*atr)
        if float(second_half["close"].max()) < resistance + 0.5 * atr:
            return None
        cur_close = float(win["close"].iloc[-1])
        cur_low = float(win["low"].iloc[-1])
        # Pullback: current low touched resistance ±0.5*atr
        if not (resistance - 0.5 * atr <= cur_low <= resistance + 0.5 * atr):
            return None
        # Bounce: current close above resistance
        if cur_close <= resistance:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.7,
            confidence=0.6,
            evidence={
                "resistance": resistance,
                "current_low": cur_low,
                "current_close": cur_close,
                "atr": atr,
            },
        )
