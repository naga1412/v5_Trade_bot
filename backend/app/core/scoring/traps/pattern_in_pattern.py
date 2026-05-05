"""pattern_in_pattern trap — small pattern nested inside larger one (medium, both).

Simplified detection per spec §10 (use available infrastructure rather than
full pattern-window tracking):

  - Layer-2 (chart patterns) fired in the proposed direction at the current
    bar (layer_scores[2] is not None and matches), AND
  - The current bar is an "inside bar" of a larger encompassing bar within
    the trailing 5 bars (i.e. some bar B in [N-5, N-1] strictly contains
    the current bar's high/low range).

This is a proxy for "small pattern inside larger pattern" — the inside-bar
geometry suggests the trader is fading a coil within a wider structure.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_LOOKBACK: int = 5


class PatternInPatternTrap:
    """Fire when a chart-pattern signal and an inside-bar coincide."""

    trap_id: str = "pattern_in_pattern"
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

        l2 = layer_scores.get(2)
        if l2 is None or l2.direction is not proposed_direction:
            return None

        cur_high = float(bars["high"].iloc[current_idx])
        cur_low = float(bars["low"].iloc[current_idx])

        encompassing_idx: int | None = None
        for i in range(current_idx - 1, max(-1, current_idx - _LOOKBACK - 1), -1):
            if i < 0:
                break
            outer_high = float(bars["high"].iloc[i])
            outer_low = float(bars["low"].iloc[i])
            if outer_high > cur_high and outer_low < cur_low:
                encompassing_idx = i
                break

        if encompassing_idx is None:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"L2 pattern signal in {proposed_direction.value}, "
                f"current bar inside larger bar at idx {encompassing_idx}"
            ),
            evidence={
                "encompassing_idx": encompassing_idx,
                "current_high": cur_high,
                "current_low": cur_low,
                "outer_high": float(bars["high"].iloc[encompassing_idx]),
                "outer_low": float(bars["low"].iloc[encompassing_idx]),
                "l2_direction": l2.direction.value,
            },
        )
