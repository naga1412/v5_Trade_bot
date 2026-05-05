"""Layer 10 - RL brain placeholder (SP-5 spec section 3 decision 8).

Returns ``None`` until SP-4 trains a PPO meta-brain. The brain's output is a
*multiplier* (BRAIN_ADJUST), not a layer score - so this module's ``score()``
returns None and SP-4 instead supplies a ``brain_adjust: float`` to
``aggregator.aggregate(...)``. The placeholder still exists so the
``layer_scores`` dict carries slot 10 with ``None`` (preserving the 1..10 shape).
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.types import LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:  # noqa: ARG001 - stub
    """Placeholder - SP-4's PPO inference supplies BRAIN_ADJUST instead."""
    return None
