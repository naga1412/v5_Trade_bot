"""Inverse cup and handle — inverted U-shape (cup) + small upward handle."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class InverseCupAndHandlePattern:
    pattern_id: str = "inverse_cup_and_handle"
    pattern_type: PatternType = "chart"
    LOOKBACK = 100

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        n = len(win)
        cup_end = int(n * 0.8)
        cup_closes = win["close"].iloc[:cup_end].to_numpy(dtype=float)
        handle_closes = win["close"].iloc[cup_end:].to_numpy(dtype=float)
        xs = np.arange(len(cup_closes), dtype=float)
        a, _b, _c = np.polyfit(xs, cup_closes, 2)
        if a >= -1e-6:
            return None
        rim = min(cup_closes[0], cup_closes[-1])
        peak = float(cup_closes.max())
        if (peak - rim) / rim < 0.05:
            return None
        handle_range = float(handle_closes.max() - handle_closes.min())
        if handle_range > (peak - rim) * 0.5:
            return None
        # Handle must have risen above its start — validates the bearish re-test.
        if float(handle_closes.max()) <= float(handle_closes[0]):
            return None
        # Entry trigger: close must break DOWN through the cup rim.
        # Firing while the handle is still rising (old behaviour) is a pre-breakdown entry.
        close = float(handle_closes[-1])
        if close >= rim:
            return None
        breakdown_pct = (rim - close) / rim
        strength = round(min(0.95, 0.70 + breakdown_pct * 25.0), 2)
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="SHORT",
            strength=strength,
            confidence=0.75,
            evidence={
                "cup_curvature": float(a),
                "rim": float(rim),
                "peak": peak,
                "handle_range": handle_range,
                "breakdown_pct": round(breakdown_pct * 100, 2),
            },
        )
