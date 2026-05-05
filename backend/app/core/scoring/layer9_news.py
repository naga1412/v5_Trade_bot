"""Layer 9 - News + sentiment placeholder (SP-5 spec section 3 decision 7).

Returns ``None`` until SP-9 wires FinBERT + news API ingest.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.types import LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:  # noqa: ARG001 - stub
    """Placeholder - populated by SP-9."""
    return None
