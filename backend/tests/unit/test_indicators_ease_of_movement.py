import math
import numpy as np
import pytest

from app.core.indicators.ease_of_movement import ease_of_movement


def test_eom_positive_on_clean_uptrend() -> None:
    n = 30
    base = np.linspace(100.0, 200.0, n)
    highs = base + 1.0
    lows = base - 1.0
    vols = np.full(n, 1_000_000.0)
    out = ease_of_movement(highs, lows, vols, period=14)
    assert out[-1] > 0


def test_eom_negative_on_clean_downtrend() -> None:
    n = 30
    base = np.linspace(200.0, 100.0, n)
    highs = base + 1.0
    lows = base - 1.0
    vols = np.full(n, 1_000_000.0)
    out = ease_of_movement(highs, lows, vols, period=14)
    assert out[-1] < 0


def test_eom_leading_nan() -> None:
    n = 30
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    vols = np.full(n, 1000.0)
    out = ease_of_movement(highs, lows, vols, period=14)
    for i in range(13):
        assert math.isnan(out[i])


def test_eom_validates_shapes() -> None:
    with pytest.raises(ValueError):
        ease_of_movement(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
