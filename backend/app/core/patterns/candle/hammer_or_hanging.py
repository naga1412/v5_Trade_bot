"""Composite Hammer/Hanging Man — context-aware bull/bear single-bar reversal.

This pattern combines TA-Lib ``CDLHAMMER`` and ``CDLHANGINGMAN`` (which share
the same body shape: small body in the upper third, long lower wick, no upper
wick) and resolves the ambiguity using a simple trend heuristic:

* If TA-Lib fires either function and the current bar's close is **below** the
  20-bar simple moving average (i.e. we're in a downtrend / pull-back), we
  treat the formation as a **hammer** and emit a ``LONG`` fire.
* Otherwise (close at or above the 20-bar SMA — uptrend / consolidation), we
  treat it as a **hanging man** and emit a ``SHORT`` fire.

If neither TA-Lib function fires, we return ``None``. If fewer than 20 bars of
history are available we cannot compute the SMA reliably and return ``None``.
"""
from __future__ import annotations

import pandas as pd
import talib  # type: ignore[import-untyped]

from app.core.patterns.base import Direction, PatternFire

_SMA_LOOKBACK = 20
_CONFIDENCE = 0.7


class HammerOrHangingPattern:
    pattern_type: str = "candle"

    def __init__(self) -> None:
        self.pattern_id: str = "hammer_or_hanging"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < 0 or current_idx >= len(bars):
            return None
        # Need at least SMA_LOOKBACK bars of preceding history (plus current).
        if current_idx < _SMA_LOOKBACK:
            return None
        try:
            o = bars["open"].to_numpy(dtype=float)
            h = bars["high"].to_numpy(dtype=float)
            low = bars["low"].to_numpy(dtype=float)
            c = bars["close"].to_numpy(dtype=float)
            hammer_out = talib.CDLHAMMER(o, h, low, c)
            hanging_out = talib.CDLHANGINGMAN(o, h, low, c)
        except Exception:  # pragma: no cover — never propagate
            return None

        hammer_val = int(hammer_out[current_idx])
        hanging_val = int(hanging_out[current_idx])
        if hammer_val == 0 and hanging_val == 0:
            return None

        # Trend context: SMA(close, 20) over the bars up to (and including) current_idx.
        window = c[current_idx - _SMA_LOOKBACK + 1 : current_idx + 1]
        sma = float(window.mean())
        current_close = float(c[current_idx])

        if current_close < sma:
            direction: Direction = "LONG"
            talib_used = "CDLHAMMER"
            raw_val = hammer_val if hammer_val != 0 else hanging_val
        else:
            direction = "SHORT"
            talib_used = "CDLHANGINGMAN"
            raw_val = hanging_val if hanging_val != 0 else hammer_val

        strength = min(1.0, abs(raw_val) / 100.0)
        return PatternFire(
            pattern_id=self.pattern_id,
            direction=direction,
            strength=strength,
            confidence=_CONFIDENCE,
            evidence={
                "talib_hammer_output": hammer_val,
                "talib_hanging_output": hanging_val,
                "talib_func": talib_used,
                "sma_20": sma,
                "close": current_close,
                "trend": "below_sma" if current_close < sma else "at_or_above_sma",
            },
        )
