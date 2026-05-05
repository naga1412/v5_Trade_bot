import math
import numpy as np
import pytest

from app.core.indicators.keltner import keltner


def test_keltner_ordering_upper_middle_lower() -> None:
    rng = np.random.default_rng(11)
    n = 60
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    upper, middle, lower = keltner(highs, lows, closes)
    valid = ~np.isnan(upper) & ~np.isnan(middle) & ~np.isnan(lower)
    assert (upper[valid] >= middle[valid]).all()
    assert (middle[valid] >= lower[valid]).all()


def test_keltner_band_distance_is_symmetric() -> None:
    rng = np.random.default_rng(12)
    n = 60
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    upper, middle, lower = keltner(highs, lows, closes, multiplier=2.0)
    valid = ~np.isnan(upper) & ~np.isnan(lower)
    np.testing.assert_allclose(
        upper[valid] - middle[valid], middle[valid] - lower[valid], rtol=1e-10
    )


def test_keltner_leading_nan() -> None:
    n = 50
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    closes = highs - 0.5
    upper, _, _ = keltner(highs, lows, closes)
    assert math.isnan(upper[0])


def test_keltner_validates_inputs() -> None:
    with pytest.raises(ValueError):
        keltner(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
