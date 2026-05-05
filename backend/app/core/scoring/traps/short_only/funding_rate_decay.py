"""funding_rate_decay trap — deeply negative funding warns SHORT (high, short).

Fires when proposed_direction is SHORT and `context.funding_rate < -0.001`.

Negative funding means shorts are paying longs to hold positions — late
entrants paying to chase. Historically this precedes short capitulation +
mean-reversion squeezes; piling in here trades poor R:R.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_FUNDING_THRESHOLD: float = -0.001


class FundingRateDecayTrap:
    """Fire when SHORT-into deeply negative funding (< -0.1%)."""

    trap_id: str = "funding_rate_decay"
    severity: Severity = "high"
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

        funding = context.funding_rate
        if funding is None or funding >= _FUNDING_THRESHOLD:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"SHORT into funding_rate={funding:.4f} < {_FUNDING_THRESHOLD} "
                "(shorts paying — capitulation/squeeze risk)"
            ),
            evidence={
                "funding_rate": funding,
                "threshold": _FUNDING_THRESHOLD,
            },
        )
