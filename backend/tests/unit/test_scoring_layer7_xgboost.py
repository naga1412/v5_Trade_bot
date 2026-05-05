"""L7 placeholder - SP-1.5 will populate."""
from __future__ import annotations

import pandas as pd

from app.core.scoring.layer7_xgboost import score


def test_returns_none() -> None:
    bars = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []}
    )
    assert score(bars) is None


def test_returns_none_on_realistic_bars() -> None:
    bars = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1000.0, 1200.0],
        }
    )
    assert score(bars) is None
