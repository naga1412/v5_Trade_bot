"""Gann fan signal — simplified: price crossing the 1×1 (45°-equivalent) line.

A canonical Gann fan needs a fixed origin pivot. We pick the most recent prior
swing (peak or trough) inside the window as the origin, derive a 1×1 line
slope from window ATR, and signal when the latest close just crossed the line.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import Direction, PatternFire, PatternType
from app.core.patterns.chart._helpers import (
    find_swing_highs,
    find_swing_lows,
    recent_atr,
)


class GannFanSignalPattern:
    pattern_id: str = "gann_fan_signal"
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
        atr = recent_atr(bars, current_idx, period=14)
        if atr <= 0 or (not peaks and not troughs):
            return None
        # Origin = most recent prior swing — uptrend if from trough, downtrend if peak
        origin_idx = max(peaks[-1] if peaks else -1, troughs[-1] if troughs else -1)
        if origin_idx < 0 or origin_idx >= len(win) - 5:
            return None
        from_trough = origin_idx in troughs
        origin_price = lows[origin_idx] if from_trough else highs[origin_idx]
        # 1×1 line: slope = ±0.1 ATR per bar (canonical Gann fan lines come in
        # 1/8, 2/8, ... slopes; 0.1 ATR/bar is a reasonable mid-fan choice).
        bars_since = (len(win) - 1) - origin_idx
        slope = (atr * 0.1) if from_trough else -(atr * 0.1)
        line_price = origin_price + slope * bars_since
        cur_close = float(win["close"].iloc[-1])
        # Signal when the current bar prints a strong move in the fan's direction
        # (one ATR push) past the projected line — simpler than strict crossing.
        if from_trough:
            if cur_close < line_price + atr:
                return None
            direction: Direction = "LONG"
        else:
            if cur_close > line_price - atr:
                return None
            direction = "SHORT"
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=0.55,
            confidence=0.45,
            evidence={
                "origin_idx": int(origin_idx),
                "from_trough": from_trough,
                "line_price": float(line_price),
                "current_close": cur_close,
            },
        )
