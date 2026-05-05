"""L9 news placeholder - SP-9 will populate."""
from __future__ import annotations

import pandas as pd

from app.core.scoring.layer9_news import score


def test_returns_none() -> None:
    assert score(pd.DataFrame({"close": [100.0]})) is None
