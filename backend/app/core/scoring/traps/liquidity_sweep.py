"""liquidity_sweep trap — chasing a stop-hunt move (extreme, both sides).

Fires when the last bar's wick pierces a prior 20-bar swing pivot by >= 1 ATR
AND the proposed direction matches the sweep direction (i.e. the trader is
chasing the move that was likely a liquidity grab and may reverse).
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.chart._helpers import (
    find_swing_highs,
    find_swing_lows,
    recent_atr,
)
from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_LOOKBACK: int = 20
_ATR_PERIOD: int = 14


class LiquiditySweepTrap:
    """Fire when the proposed direction chases a 1-ATR sweep of a swing pivot."""

    trap_id: str = "liquidity_sweep"
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
        if current_idx < _LOOKBACK or current_idx >= len(bars):
            return None
        if proposed_direction is Direction.NEUTRAL:
            return None

        atr = recent_atr(bars, current_idx, period=_ATR_PERIOD)
        if atr <= 0:
            return None

        win = bars.iloc[current_idx - _LOOKBACK : current_idx + 1]
        highs = win["high"].to_numpy(dtype=float)
        lows = win["low"].to_numpy(dtype=float)
        last_high = float(highs[-1])
        last_low = float(lows[-1])

        sw_highs = find_swing_highs(highs[:-1], prominence=0.5, distance=3)
        sw_lows = find_swing_lows(lows[:-1], prominence=0.5, distance=3)

        # Sweep up: last bar's high pierces a prior swing high by >= 1 ATR
        if proposed_direction is Direction.LONG and sw_highs:
            ref_high = float(highs[:-1][sw_highs[-1]])
            if last_high - ref_high >= atr:
                return TrapFire(
                    trap_id=self.trap_id,
                    severity=self.severity,
                    side=self.side,
                    reason="LONG chases sweep of prior swing high by >= 1 ATR",
                    evidence={
                        "swept_level": ref_high,
                        "last_high": last_high,
                        "atr": atr,
                        "pierce": last_high - ref_high,
                    },
                )

        # Sweep down: last bar's low pierces a prior swing low by >= 1 ATR
        if proposed_direction is Direction.SHORT and sw_lows:
            ref_low = float(lows[:-1][sw_lows[-1]])
            if ref_low - last_low >= atr:
                return TrapFire(
                    trap_id=self.trap_id,
                    severity=self.severity,
                    side=self.side,
                    reason="SHORT chases sweep of prior swing low by >= 1 ATR",
                    evidence={
                        "swept_level": ref_low,
                        "last_low": last_low,
                        "atr": atr,
                        "pierce": ref_low - last_low,
                    },
                )

        return None
