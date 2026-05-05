"""volatility_regime_change trap — vol spike (medium, both sides).

Fires when the recent 14-bar ATR is more than 2x the average 14-bar ATR over
the trailing 50 bars. Direction signals are unreliable in regime breaks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.patterns.chart._helpers import recent_atr
from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_ATR_PERIOD: int = 14
_HIST_BARS: int = 50
_RATIO_THRESHOLD: float = 2.0


class VolatilityRegimeChangeTrap:
    """Fire when current ATR(14) > 2x trailing 50-bar ATR(14) average."""

    trap_id: str = "volatility_regime_change"
    severity: Severity = "medium"
    side: Side = "both"

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        if current_idx < _ATR_PERIOD + _HIST_BARS or current_idx >= len(bars):
            return None

        cur_atr = recent_atr(bars, current_idx, period=_ATR_PERIOD)
        if cur_atr <= 0:
            return None

        # Compute ATR at each historical reference index across the trailing
        # _HIST_BARS bars (excluding the current bar). Average them.
        atrs: list[float] = []
        start = current_idx - _HIST_BARS
        for i in range(start, current_idx):
            a = recent_atr(bars, i, period=_ATR_PERIOD)
            if a > 0:
                atrs.append(a)
        if not atrs:
            return None
        avg_atr = float(np.mean(atrs))
        if avg_atr <= 0:
            return None

        ratio = cur_atr / avg_atr
        if ratio < _RATIO_THRESHOLD:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"current ATR {cur_atr:.4f} = {ratio:.2f}x trailing "
                f"{_HIST_BARS}-bar avg {avg_atr:.4f}"
            ),
            evidence={
                "current_atr": cur_atr,
                "historical_avg_atr": avg_atr,
                "ratio": ratio,
                "ratio_threshold": _RATIO_THRESHOLD,
                "atr_period": _ATR_PERIOD,
                "hist_bars": _HIST_BARS,
            },
        )
