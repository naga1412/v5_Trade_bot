"""counter_weekly trap — direction opposes weekly EMA50 slope (high, both sides).

Uses a 168-bar EMA on a 1h chart as a proxy for weekly EMA50. Computes the
EMA's slope between the most recent two bars: positive slope = LONG bias,
negative slope = SHORT bias. Fires when proposed direction opposes that bias.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_EMA_PERIOD: int = 168  # 1 week of 1h bars


class CounterWeeklyTrap:
    """Fire when proposed direction opposes the 168-bar EMA slope."""

    trap_id: str = "counter_weekly"
    severity: Severity = "high"
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
        if current_idx < _EMA_PERIOD or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        closes = bars["close"].iloc[: current_idx + 1]
        ema = closes.ewm(span=_EMA_PERIOD, adjust=False).mean()
        slope = float(ema.iloc[-1] - ema.iloc[-2])

        weekly_dir: Direction
        if slope > 0:
            weekly_dir = Direction.LONG
        elif slope < 0:
            weekly_dir = Direction.SHORT
        else:
            return None

        if proposed_direction is weekly_dir:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"{proposed_direction.value} opposes weekly EMA{_EMA_PERIOD} "
                f"slope ({weekly_dir.value})"
            ),
            evidence={
                "ema_slope": slope,
                "weekly_direction": weekly_dir.value,
                "ema_period": _EMA_PERIOD,
            },
        )
