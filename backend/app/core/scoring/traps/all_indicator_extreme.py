"""all_indicator_extreme trap — chasing reversal extreme (high, both sides).

Uses RSI(14) as the canonical "indicator extreme" gauge:
  - RSI > 80 + LONG proposed → chasing overbought (likely to revert)
  - RSI < 20 + SHORT proposed → chasing oversold (likely to revert)
"""
from __future__ import annotations

import pandas as pd

from app.core.indicators.rsi import rsi as compute_rsi
from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_RSI_PERIOD: int = 14
_OVERBOUGHT: float = 80.0
_OVERSOLD: float = 20.0


class AllIndicatorExtremeTrap:
    """Fire when RSI is at an extreme and the trader chases the move."""

    trap_id: str = "all_indicator_extreme"
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
        if current_idx < _RSI_PERIOD or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        closes = bars["close"].iloc[: current_idx + 1].to_numpy(dtype=float)
        rsi_series = compute_rsi(closes, period=_RSI_PERIOD)
        last_rsi = rsi_series[-1]
        if last_rsi != last_rsi:  # NaN check (warmup)
            return None

        if proposed_direction is Direction.LONG and last_rsi > _OVERBOUGHT:
            return TrapFire(
                trap_id=self.trap_id,
                severity=self.severity,
                side=self.side,
                reason=f"RSI={last_rsi:.1f} > {_OVERBOUGHT} (LONG chases overbought)",
                evidence={
                    "rsi": float(last_rsi),
                    "threshold": _OVERBOUGHT,
                    "rsi_period": _RSI_PERIOD,
                },
            )
        if proposed_direction is Direction.SHORT and last_rsi < _OVERSOLD:
            return TrapFire(
                trap_id=self.trap_id,
                severity=self.severity,
                side=self.side,
                reason=f"RSI={last_rsi:.1f} < {_OVERSOLD} (SHORT chases oversold)",
                evidence={
                    "rsi": float(last_rsi),
                    "threshold": _OVERSOLD,
                    "rsi_period": _RSI_PERIOD,
                },
            )
        return None
