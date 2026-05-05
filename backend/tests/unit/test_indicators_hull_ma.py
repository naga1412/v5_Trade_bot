import math
import numpy as np
import pytest

from app.core.indicators.hull_ma import hull_ma


def test_hull_ma_tracks_uptrend() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    h = hull_ma(closes, period=16)
    # On a perfect linear uptrend, HMA should track price closely (low lag).
    # Step is ~1.69 per bar; HMA lag should be well under one full step.
    assert abs(h[-1] - closes[-1]) < 1.5


def test_hull_ma_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    h = hull_ma(closes, period=16)
    # First period-1 must be NaN
    for i in range(15):
        assert math.isnan(h[i])


def test_hull_ma_no_lookahead() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    full = hull_ma(closes, period=10)
    truncated = hull_ma(closes[:50], period=10)
    assert full[49] == pytest.approx(truncated[49])


def test_hull_ma_period_must_be_gt_1() -> None:
    with pytest.raises(ValueError):
        hull_ma(np.array([1.0, 2.0, 3.0]), period=1)
