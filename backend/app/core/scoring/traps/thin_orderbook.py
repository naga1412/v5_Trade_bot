"""thin_orderbook trap — illiquid market warning (medium, both sides).

Two proxy signals (either fires):
  1. context.borrow_rate_pct >= 5.0 (high borrow = limited liquidity)
  2. Trailing 24-bar volume <= 0.5x trailing 720-bar (30-day proxy on 1h bars)
     median volume.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_BORROW_THRESHOLD: float = 5.0
_RECENT_BARS: int = 24
_HIST_BARS: int = 720
_VOL_RATIO: float = 0.5


class ThinOrderbookTrap:
    """Fire on high borrow rate or anomalously low recent volume."""

    trap_id: str = "thin_orderbook"
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
        if current_idx < 0 or current_idx >= len(bars):
            return None

        # Path 1: high borrow rate signal
        borrow = context.borrow_rate_pct
        if borrow is not None and borrow >= _BORROW_THRESHOLD:
            return TrapFire(
                trap_id=self.trap_id,
                severity=self.severity,
                side=self.side,
                reason=f"borrow_rate_pct={borrow:.2f} >= {_BORROW_THRESHOLD}",
                evidence={
                    "borrow_rate_pct": borrow,
                    "threshold": _BORROW_THRESHOLD,
                    "trigger": "borrow_rate",
                },
            )

        # Path 2: low recent volume vs historical median
        if current_idx + 1 < _HIST_BARS + _RECENT_BARS:
            return None

        recent = bars["volume"].iloc[current_idx - _RECENT_BARS + 1 : current_idx + 1]
        hist = bars["volume"].iloc[current_idx - _HIST_BARS + 1 : current_idx + 1]
        recent_avg = float(recent.mean())
        hist_med = float(hist.median())
        if hist_med <= 0:
            return None
        if recent_avg > _VOL_RATIO * hist_med:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"recent {_RECENT_BARS}-bar avg vol {recent_avg:.1f} <= "
                f"{_VOL_RATIO} x median {hist_med:.1f}"
            ),
            evidence={
                "recent_avg_volume": recent_avg,
                "historical_median_volume": hist_med,
                "vol_ratio": _VOL_RATIO,
                "trigger": "low_volume",
            },
        )
