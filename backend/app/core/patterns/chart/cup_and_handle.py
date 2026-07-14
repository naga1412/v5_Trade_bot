"""Cup and handle — smooth U-shape (cup) followed by small downward consolidation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.base import PatternFire, PatternType


class CupAndHandlePattern:
    pattern_id: str = "cup_and_handle"
    pattern_type: PatternType = "chart"
    LOOKBACK = 100

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        if current_idx < self.LOOKBACK:
            return None
        win = bars.iloc[current_idx - self.LOOKBACK : current_idx + 1]
        n = len(win)
        # Cup = first 80%; handle = final 20%
        cup_end = int(n * 0.8)
        cup_closes = win["close"].iloc[:cup_end].to_numpy(dtype=float)
        handle_closes = win["close"].iloc[cup_end:].to_numpy(dtype=float)
        # Fit a parabola (concave-up) to cup
        xs = np.arange(len(cup_closes), dtype=float)
        a, _b, _c = np.polyfit(xs, cup_closes, 2)
        if a <= 1e-6:
            return None
        # Cup quality: depth ≥ 5% from rim
        rim = max(cup_closes[0], cup_closes[-1])
        bottom = float(cup_closes.min())
        if (rim - bottom) / rim < 0.05:
            return None
        # Handle: gentle consolidation, smaller than cup depth
        handle_range = float(handle_closes.max() - handle_closes.min())
        if handle_range > (rim - bottom) * 0.5:
            return None
        # Handle must have dipped below its starting point — confirms consolidation formed.
        if float(handle_closes.min()) >= float(handle_closes[0]):
            return None
        # Entry trigger: current close must break OUT above the cup rim.
        # Firing during the handle decline (old behaviour) is a top-chasing entry;
        # firing at rim breakout is the textbook signal.
        close = float(handle_closes[-1])
        if close <= rim:
            return None
        # Strength scales with breakout magnitude: base 0.70, +0.25 per 1% above rim.
        breakout_pct = (close - rim) / rim
        strength = round(min(0.95, 0.70 + breakout_pct * 25.0), 2)
        return PatternFire(
            pattern_id=self.pattern_id,
            direction="LONG",
            strength=strength,
            confidence=0.75,
            evidence={
                "cup_curvature": float(a),
                "rim": float(rim),
                "bottom": bottom,
                "handle_range": handle_range,
                "breakout_pct": round(breakout_pct * 100, 2),
            },
        )
