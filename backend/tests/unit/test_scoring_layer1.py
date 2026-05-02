import numpy as np
import pandas as pd
import pytest

from app.core.scoring.layer1_macro import score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_strong_uptrend_gives_long() -> None:
    closes = list(np.linspace(100.0, 200.0, 250))  # monotonic up
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength == pytest.approx(0.9)
    assert result.confidence == pytest.approx(0.85)


def test_strong_downtrend_gives_short() -> None:
    closes = list(np.linspace(200.0, 100.0, 250))
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    assert result.direction is Direction.SHORT


def test_choppy_below_ema200_gives_weak_short() -> None:
    # 250 bars: first 200 around 100, last 50 oscillate around 90
    closes = [100.0] * 200 + [90.0 + (1 if i % 2 else -1) for i in range(50)]
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    # Either weak short or neutral; at minimum not strong long
    assert result.direction is not Direction.LONG or result.strength < 0.7


def test_insufficient_data_returns_none() -> None:
    bars = make_bars([100.0] * 50)  # need 200 bars for EMA200
    result = score(bars)
    assert result is None
