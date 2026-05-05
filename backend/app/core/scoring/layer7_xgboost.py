"""Layer 7 - XGBoost placeholder (SP-1.5 will populate).

Per SP-5 spec decision 5: returns ``None`` until SP-1.5 trains an XGBoost on
the 43-indicator feature vector and wires inference here. The aggregator
treats ``None`` as 'layer absent' and redistributes weight across active layers.
"""
from __future__ import annotations

import pandas as pd

from app.core.scoring.types import LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:  # noqa: ARG001 - stub
    """Placeholder - populated by SP-1.5."""
    return None
