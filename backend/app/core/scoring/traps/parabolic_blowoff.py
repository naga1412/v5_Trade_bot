"""parabolic_blowoff trap — chasing exhaustion move (extreme, both sides).

Fires when the last 3-5 bars are all in the proposed direction AND their
average body size is >= 1.5x ATR (a vertical run that's likely to reverse).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.chart._helpers import recent_atr
from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_BAR_RUN: int = 3
_BODY_MULT: float = 1.5
_ATR_PERIOD: int = 14


class ParabolicBlowoffTrap:
    """Fire when 3+ same-direction bars with avg body >= 1.5x ATR run vertical."""

    trap_id: str = "parabolic_blowoff"
    severity: Severity = "extreme"
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
        if current_idx < _BAR_RUN or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        atr = recent_atr(bars, current_idx, period=_ATR_PERIOD)
        if atr <= 0:
            return None

        win = bars.iloc[current_idx - _BAR_RUN + 1 : current_idx + 1]
        opens = win["open"].to_numpy(dtype=float)
        closes = win["close"].to_numpy(dtype=float)
        deltas = closes - opens

        if proposed_direction is Direction.LONG and not all(d > 0 for d in deltas):
            return None
        if proposed_direction is Direction.SHORT and not all(d < 0 for d in deltas):
            return None

        avg_body = float(abs(deltas).mean())
        if avg_body < _BODY_MULT * atr:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"{_BAR_RUN} consecutive {proposed_direction.value} bars "
                f"avg body {avg_body:.4f} >= {_BODY_MULT} * ATR ({atr:.4f})"
            ),
            evidence={
                "n_bars": _BAR_RUN,
                "avg_body": avg_body,
                "atr": atr,
                "body_mult": _BODY_MULT,
            },
        )
