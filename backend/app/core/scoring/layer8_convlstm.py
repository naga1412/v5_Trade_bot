"""Layer 8 - Conv-LSTM hookup (SP-5 spec section 3 decision 6).

When SP-1's live worker has populated the ``predictions.ghost_*`` columns for
the current bar, the predictor passes a ``GhostInput`` here and this layer
emits a directional vote. When no ghost is present, returns ``None``.

The ``GhostInput`` dataclass is intentionally minimal so callers don't have
to import the full ``LivePredictionOut`` / ``GhostOut`` schema chain into
the scoring layer (avoids an import cycle with ``app.ml.inference``).
``app/predictor.py`` is responsible for translating its ``GhostCandle`` /
prediction dict into ``GhostInput`` before calling this layer.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.core.scoring.types import Direction, LayerScore

NEUTRAL_DELTA: float = 0.0001  # ~ 0.01 %


@dataclass(frozen=True)
class GhostInput:
    """Minimal carrier for the Conv-LSTM ghost prediction.

    Attributes:
        ghost_close: predicted close price for the next bar.
        ghost_uncertainty: model uncertainty in [0, +inf); larger = less
            confident. Confidence is computed as
            ``max(0.3, min(1.0, 1.0 - ghost_uncertainty))``.
    """
    ghost_close: float
    ghost_uncertainty: float


def score(
    bars: pd.DataFrame, *, ghost: GhostInput | None
) -> LayerScore | None:
    if ghost is None or len(bars) == 0:
        return None
    current_close = float(bars["close"].iloc[-1])
    if current_close <= 0:
        return None
    delta_pct = (ghost.ghost_close - current_close) / current_close
    if abs(delta_pct) < NEUTRAL_DELTA:
        return LayerScore(
            direction=Direction.NEUTRAL,
            strength=0.0,
            confidence=max(0.3, min(1.0, 1.0 - ghost.ghost_uncertainty)),
            notes=f"ghost_close==current_close ({current_close:.2f})",
        )
    direction = Direction.LONG if delta_pct > 0 else Direction.SHORT
    strength = min(1.0, abs(delta_pct) * 10.0)
    confidence = max(0.3, min(1.0, 1.0 - ghost.ghost_uncertainty))
    notes = (
        f"ghost {ghost.ghost_close:.2f} vs close {current_close:.2f} "
        f"({delta_pct * 100:+.2f}%)"
    )
    return LayerScore(
        direction=direction,
        strength=strength,
        confidence=confidence,
        notes=notes,
    )
