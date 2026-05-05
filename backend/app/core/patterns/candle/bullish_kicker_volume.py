"""Bullish kicker with volume confirmation — bearish bar followed by gap-up bullish bar.

Kicker definition (bullish flavour):
- prior bar is bearish (close < open)
- current bar is bullish (close > open)
- current bar opens at or above the prior bar's open (gap up across the body)
- current bar's volume ≥ 2 × the trailing 20-bar mean volume
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.base import PatternFire, PatternType
from app.core.patterns.candle._helpers import is_bearish, is_bullish

_VOLUME_RATIO = 2.0
_VOL_LOOKBACK = 20


class BullishKickerVolumePattern:
    pattern_id: str = "bullish_kicker_volume"
    pattern_type: PatternType = "candle"

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < _VOL_LOOKBACK or current_idx >= len(bars):
            return None
        prev = bars.iloc[current_idx - 1]
        cur = bars.iloc[current_idx]
        if not (is_bearish(prev) and is_bullish(cur)):
            return None
        if float(cur["open"]) < float(prev["open"]):
            return None
        vol_window = bars["volume"].iloc[
            current_idx - _VOL_LOOKBACK : current_idx
        ]
        avg_vol = float(vol_window.mean())
        cur_vol = float(cur["volume"])
        if avg_vol <= 0 or cur_vol < _VOLUME_RATIO * avg_vol:
            return None
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=min(1.0, cur_vol / (avg_vol * _VOLUME_RATIO * 2.0)),
            confidence=0.8,
            evidence={
                "volume_ratio": cur_vol / avg_vol,
                "gap": float(cur["open"]) - float(prev["open"]),
            },
        )
