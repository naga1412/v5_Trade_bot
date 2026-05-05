"""short_squeeze_cascade trap — SHORT into a vertical run with OI growing (high, short).

Fires when:
  * proposed_direction is SHORT, AND
  * the last 3 bars are all bullish with average body > ATR
    (vertical run = squeeze building), AND
  * `context.open_interest_delta_24h` is not None and > 0.20
    (OI up 20% in 24h = stops being hit / new shorts piling in).

Shorting into this is high risk: the very flow that's painting the move is
liquidating shorts above, and another leg up cascades.
"""
from __future__ import annotations

import pandas as pd

from app.core.patterns.chart._helpers import recent_atr
from app.core.scoring.traps.base import Severity, Side, TrapContext, TrapFire
from app.core.scoring.types import Direction, LayerScore

_BAR_RUN: int = 3
_OI_THRESHOLD: float = 0.20
_ATR_PERIOD: int = 14


class ShortSqueezeCascadeTrap:
    """Fire when SHORT-into a bullish vertical run + OI growing > 20%/24h."""

    trap_id: str = "short_squeeze_cascade"
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

        oi_delta = context.open_interest_delta_24h
        if oi_delta is None or oi_delta <= _OI_THRESHOLD:
            return None

        if current_idx < _BAR_RUN or current_idx >= len(bars):
            return None

        atr = recent_atr(bars, current_idx, period=_ATR_PERIOD)
        if atr <= 0:
            return None

        win = bars.iloc[current_idx - _BAR_RUN + 1 : current_idx + 1]
        opens = win["open"].to_numpy(dtype=float)
        closes = win["close"].to_numpy(dtype=float)
        deltas = closes - opens

        if not all(d > 0 for d in deltas):
            return None

        avg_body = float(abs(deltas).mean())
        if avg_body <= atr:
            return None

        return TrapFire(
            trap_id=self.trap_id,
            severity=self.severity,
            side=self.side,
            reason=(
                f"SHORT into {_BAR_RUN}-bar bullish run avg_body={avg_body:.4f} "
                f"> ATR={atr:.4f} with OI +{oi_delta * 100:.1f}%/24h"
            ),
            evidence={
                "n_bars": _BAR_RUN,
                "avg_body": avg_body,
                "atr": atr,
                "open_interest_delta_24h": oi_delta,
                "oi_threshold": _OI_THRESHOLD,
            },
        )
