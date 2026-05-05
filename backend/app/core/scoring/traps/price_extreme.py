"""price_extreme trap — chasing range extremes (medium, both sides).

Fires when:
  - LONG proposed and current close within 0.5% of trailing 200-bar high.
  - SHORT proposed and current close within 0.5% of trailing 200-bar low.

The classic "buying the top / selling the bottom" trap.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_LOOKBACK: int = 200
_PROXIMITY_PCT: float = 0.005  # 0.5%


class PriceExtremeTrap:
    """Fire when current close is within 0.5% of the 200-bar extreme."""

    trap_id: str = "price_extreme"
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
        if current_idx < _LOOKBACK or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        win = bars.iloc[current_idx - _LOOKBACK + 1 : current_idx + 1]
        last_close = float(bars["close"].iloc[current_idx])
        win_high = float(win["high"].max())
        win_low = float(win["low"].min())

        if proposed_direction is Direction.LONG and win_high > 0:
            distance = (win_high - last_close) / win_high
            if 0 <= distance <= _PROXIMITY_PCT:
                return TrapFire(
                    trap_id=self.trap_id,
                    severity=self.severity,
                    side=self.side,
                    reason=(
                        f"LONG within {distance*100:.2f}% of {_LOOKBACK}-bar high "
                        f"{win_high:.2f}"
                    ),
                    evidence={
                        "extreme": win_high,
                        "last_close": last_close,
                        "distance_pct": distance,
                        "proximity_threshold": _PROXIMITY_PCT,
                        "side": "high",
                    },
                )

        if proposed_direction is Direction.SHORT and win_low > 0:
            distance = (last_close - win_low) / win_low
            if 0 <= distance <= _PROXIMITY_PCT:
                return TrapFire(
                    trap_id=self.trap_id,
                    severity=self.severity,
                    side=self.side,
                    reason=(
                        f"SHORT within {distance*100:.2f}% of {_LOOKBACK}-bar low "
                        f"{win_low:.2f}"
                    ),
                    evidence={
                        "extreme": win_low,
                        "last_close": last_close,
                        "distance_pct": distance,
                        "proximity_threshold": _PROXIMITY_PCT,
                        "side": "low",
                    },
                )

        return None
