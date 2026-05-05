"""L10 brain placeholder - SP-4 will populate via BRAIN_ADJUST."""
from __future__ import annotations

import pandas as pd

from app.core.scoring.layer10_brain import score


def test_returns_none() -> None:
    assert score(pd.DataFrame({"close": [100.0]})) is None
