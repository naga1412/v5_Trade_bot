"""Multi-TF confluence long — short-term close above multiple long-term EMAs.

Single-TF approximation per dispatch spec: EMA20 > EMA50 > EMA200, close > EMA20.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


def _ema(arr: np.ndarray, period: int) -> float:
    if len(arr) < period:
        return float("nan")
    alpha = 2.0 / (period + 1)
    ema = arr[0]
    for x in arr[1:]:
        ema = alpha * x + (1 - alpha) * ema
    return float(ema)


class MultiTfConfluenceLongPattern:
    pattern_id: str = "multi_tf_confluence_long"
    pattern_type: PatternType = "chart"
    LOOKBACK = 200

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        closes = bars["close"].iloc[: current_idx + 1].to_numpy(dtype=float)
        ema20 = _ema(closes[-20:], 20)
        ema50 = _ema(closes[-50:], 50)
        ema200 = _ema(closes[-200:], 200)
        if not (ema20 > ema50 > ema200):
            return None
        cur_close = float(closes[-1])
        if cur_close <= ema20:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=0.65,
            confidence=0.6,
            evidence={
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200,
                "current_close": cur_close,
            },
        )
