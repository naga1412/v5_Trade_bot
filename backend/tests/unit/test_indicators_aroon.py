import math
import numpy as np
import pytest

from app.core.indicators.aroon import aroon


def test_aroon_up_100_when_current_bar_is_highest() -> None:
    n = 20
    base = np.linspace(100.0, 200.0, n)  # strict uptrend → last is HH
    highs = base + 1.0
    lows = base - 1.0
    up, down = aroon(highs, lows, period=14)
    assert up[-1] == pytest.approx(100.0)


def test_aroon_down_100_when_current_bar_is_lowest() -> None:
    n = 20
    base = np.linspace(200.0, 100.0, n)  # strict downtrend → last is LL
    highs = base + 1.0
    lows = base - 1.0
    up, down = aroon(highs, lows, period=14)
    assert down[-1] == pytest.approx(100.0)


def test_aroon_in_range_0_100() -> None:
    rng = np.random.default_rng(33)
    n = 60
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    up, down = aroon(highs, lows)
    for series in (up, down):
        valid = series[~np.isnan(series)]
        assert (valid >= 0).all() and (valid <= 100).all()


def test_aroon_leading_nan() -> None:
    n = 30
    highs = np.linspace(100.0, 200.0, n)
    lows = highs - 1.0
    up, _ = aroon(highs, lows, period=14)
    for i in range(14):
        assert math.isnan(up[i])
