"""borrow_rate trap — extreme borrow APR makes shorting unprofitable (high, short).

Fires when proposed_direction is SHORT and `context.borrow_rate_pct > 50.0`.

When borrow > 50% APR, financing alone burns ~14 bps/day. Combined with
slippage and funding, the carry usually exceeds the realistic R:R of the
swing — and high borrow itself is a signal that the broader market is short
the asset, often the wrong side.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_BORROW_THRESHOLD: float = 50.0


class BorrowRateTrap:
    """Fire when SHORT-into borrow rate APR > 50%."""

    trap_id: str = "borrow_rate"
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

        borrow = context.borrow_rate_pct
        if borrow is None or borrow <= _BORROW_THRESHOLD:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"SHORT into borrow_rate_pct={borrow:.1f}% > {_BORROW_THRESHOLD}% "
                "(carry burns R:R; crowded short signal)"
            ),
            evidence={
                "borrow_rate_pct": borrow,
                "threshold": _BORROW_THRESHOLD,
            },
        )
