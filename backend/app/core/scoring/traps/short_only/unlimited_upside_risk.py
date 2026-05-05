"""unlimited_upside_risk trap — SHORT-into recent ATH = bad R:R (medium, short).

Fires when proposed_direction is SHORT and the current close is within 2%
of the trailing 365-bar high.

Selling at recent highs caps your upside at 100% (price → 0) but exposes
you to unbounded loss if the breakout follows through. The R:R is
asymmetric the wrong way, regardless of how clean the setup looks.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_LOOKBACK: int = 365
_PROXIMITY_PCT: float = 0.02  # 2%


class UnlimitedUpsideRiskTrap:
    """Fire when SHORT-into close within 2% of trailing 365-bar high."""

    trap_id: str = "unlimited_upside_risk"
    severity: Severity = "medium"
    side: Side = "short"

    def check(
        self,
        bars: pd.DataFrame,
        *,
        current_idx: int,
        layer_scores: dict[int, LayerScore | None],
        proposed_direction: Direction,
        context: TrapContext,
    ) -> TrapFire | None:
        if proposed_direction is not Direction.SHORT:
            return None
        if current_idx < _LOOKBACK or current_idx >= len(bars):
            return None

        win = bars.iloc[current_idx - _LOOKBACK + 1 : current_idx + 1]
        last_close = float(bars["close"].iloc[current_idx])
        trailing_high = float(win["high"].max())

        if trailing_high <= 0:
            return None

        distance = (trailing_high - last_close) / trailing_high
        if not (0 <= distance <= _PROXIMITY_PCT):
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"SHORT within {distance * 100:.2f}% of {_LOOKBACK}-bar high "
                f"{trailing_high:.4f} (asymmetric R:R)"
            ),
            evidence={
                "trailing_high": trailing_high,
                "last_close": last_close,
                "distance_pct": distance,
                "proximity_threshold": _PROXIMITY_PCT,
                "lookback": _LOOKBACK,
            },
        )
