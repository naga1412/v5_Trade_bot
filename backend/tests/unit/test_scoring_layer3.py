import numpy as np
import pandas as pd

from app.core.scoring.layer3_momentum import score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_strong_up_momentum_gives_long() -> None:
    closes = list(np.linspace(100.0, 200.0, 100))  # smooth uptrend
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength > 0.5


def test_strong_down_momentum_gives_short() -> None:
    closes = list(np.linspace(200.0, 100.0, 100))
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.SHORT
    assert result.strength > 0.5


def test_flat_market_gives_neutral() -> None:
    closes = [150.0 + (1 if i % 2 else -1) * 0.1 for i in range(100)]
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.NEUTRAL


def test_insufficient_data_returns_none() -> None:
    closes = [100.0] * 20
    assert score(make_bars(closes)) is None
