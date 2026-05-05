import math
import numpy as np
import pytest

from app.core.indicators.donchian import donchian


def test_donchian_exact_rolling_max_min() -> None:
    highs = np.array([10.0, 12.0, 11.0, 15.0, 13.0, 14.0])
    lows = np.array([8.0, 9.0, 7.0, 10.0, 11.0, 12.0])
    upper, middle, lower = donchian(highs, lows, period=3)
    # upper[2] = max(10,12,11) = 12 ; lower[2] = min(8,9,7) = 7 ; mid = 9.5
    assert upper[2] == pytest.approx(12.0)
    assert lower[2] == pytest.approx(7.0)
    assert middle[2] == pytest.approx(9.5)
    # upper[5] = max(11, 15, 13, 14)... period=3 → indices 3..5 → max=15
    assert upper[5] == pytest.approx(15.0)
    assert lower[5] == pytest.approx(10.0)


def test_donchian_leading_nan() -> None:
    highs = np.linspace(10.0, 20.0, 10)
    lows = highs - 1.0
    upper, _, _ = donchian(highs, lows, period=5)
    for i in range(4):
        assert math.isnan(upper[i])


def test_donchian_validates_shapes() -> None:
    with pytest.raises(ValueError):
        donchian(np.array([1.0]), np.array([1.0, 2.0]))


def test_donchian_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        donchian(np.array([1.0]), np.array([1.0]), period=0)
