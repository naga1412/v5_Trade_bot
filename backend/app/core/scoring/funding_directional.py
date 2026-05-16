"""Funding-rate directional adjustment helper for the live scoring stack (PR1).

One pure function:
  - compute_funding_directional_adj: signed ±0.10 boost when funding-rate
    imbalance aligns with the signal direction.

No I/O. Caller supplies the funding rate (from intermarket snapshot) and
the signal direction; this module returns the adjustment.
"""
from __future__ import annotations

from app.core.scoring.types import Direction

# ---------------------------------------------------------------------------
# Constants (operator-locked)
# ---------------------------------------------------------------------------

FUNDING_DEADBAND: float = 0.0005  # ±0.05% per 8h funding event
BOOST: float = 0.10               # always +0.10 when active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_funding_directional_adj(
    funding_rate: float | None,
    signal_direction: Direction,
) -> float | None:
    """Compute the signed funding-direction adjustment for the live score.

    Returns:
        +0.10 when funding_rate < -FUNDING_DEADBAND AND direction is LONG
        +0.10 when funding_rate > +FUNDING_DEADBAND AND direction is SHORT
         0.0  when |funding_rate| <= FUNDING_DEADBAND (deadband)
        None  when funding_rate is None (no intermarket snapshot) OR
              when direction is NEUTRAL (signal doesn't take direction)

    The sign convention is "boost towards the side that benefits from
    the funding-rate imbalance":
      - Negative funding (short-side pays long-side) → boost LONG signal.
      - Positive funding (long-side pays short-side) → boost SHORT signal.

    Boundary semantics (operator-locked):
      - |funding_rate| > FUNDING_DEADBAND  triggers boost (strict gt).
      - |funding_rate| == FUNDING_DEADBAND exactly → 0.0 (deadband).
      - funding_rate == 0.0 → 0.0 (deadband).

    Examples:
        funding_rate=+0.0008, direction=Direction.SHORT → +0.10
        funding_rate=-0.0008, direction=Direction.LONG  → +0.10
        funding_rate=+0.0002, direction=Direction.SHORT → 0.0 (deadband)
        funding_rate=None or direction=Direction.NEUTRAL → None
    """
    if funding_rate is None:
        return None

    if signal_direction is Direction.NEUTRAL:
        return None

    # Negative funding → longs receive payment → boost LONG only.
    if funding_rate < -FUNDING_DEADBAND and signal_direction is Direction.LONG:
        return BOOST

    # Positive funding → shorts receive payment → boost SHORT only.
    if funding_rate > FUNDING_DEADBAND and signal_direction is Direction.SHORT:
        return BOOST

    # Deadband (includes exact boundary, zero, and wrong-side cases).
    return 0.0
