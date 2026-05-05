"""volume_no_followthrough trap — high-volume bar without follow-through (high, both).

Fires when:
  - bar N-1 had volume >= 2x the 20-bar trailing average, AND
  - bar N has body <= 0.3x bar N-1 body (no continuation)

Signals exhausted demand/supply — the spike absorbed everyone willing to push.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_VOL_LOOKBACK: int = 20
_VOL_MULT: float = 2.0
_BODY_RATIO: float = 0.3


class VolumeNoFollowthroughTrap:
    """Fire when a high-volume bar is followed by a tiny-body bar."""

    trap_id: str = "volume_no_followthrough"
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
        if current_idx < _VOL_LOOKBACK + 1 or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        prev_idx = current_idx - 1
        prev_vol = float(bars["volume"].iloc[prev_idx])
        # 20-bar avg ending at prev_idx - 1 (does not include the spike itself)
        avg_window = bars["volume"].iloc[prev_idx - _VOL_LOOKBACK : prev_idx]
        avg_vol = float(avg_window.mean())
        if avg_vol <= 0 or prev_vol < _VOL_MULT * avg_vol:
            return None

        prev_body = abs(
            float(bars["close"].iloc[prev_idx]) - float(bars["open"].iloc[prev_idx])
        )
        cur_body = abs(
            float(bars["close"].iloc[current_idx]) - float(bars["open"].iloc[current_idx])
        )
        if prev_body <= 0:
            return None
        if cur_body > _BODY_RATIO * prev_body:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"prev volume {prev_vol:.1f} >= {_VOL_MULT}x avg {avg_vol:.1f}; "
                f"current body {cur_body:.4f} <= {_BODY_RATIO} x prev body {prev_body:.4f}"
            ),
            evidence={
                "prev_volume": prev_vol,
                "avg_volume": avg_vol,
                "vol_mult": _VOL_MULT,
                "prev_body": prev_body,
                "current_body": cur_body,
                "body_ratio": _BODY_RATIO,
            },
        )
