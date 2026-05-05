import math
import numpy as np
import pytest

from app.core.indicators.mass_index import mass_index


def test_mass_index_typical_range() -> None:
    rng = np.random.default_rng(20)
    n = 100
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + np.abs(rng.normal(0, 1, n)) + 0.5
    lows = base - np.abs(rng.normal(0, 1, n)) - 0.5
    out = mass_index(highs, lows)
    valid = out[~np.isnan(out)]
    assert valid.size > 0
    # Mass Index typically falls in [20, 35]
    assert (valid >= 15).all() and (valid <= 50).all()


def test_mass_index_constant_range_equals_25() -> None:
    n = 80
    highs = np.full(n, 101.0)
    lows = np.full(n, 99.0)
    out = mass_index(highs, lows)
    valid = out[~np.isnan(out)]
    # Constant range → ratio = 1 every bar → sum over 25 bars = 25.0
    np.testing.assert_allclose(valid, 25.0, atol=1e-9)


def test_mass_index_leading_nan() -> None:
    n = 60
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    out = mass_index(highs, lows)
    assert math.isnan(out[0])
    assert math.isnan(out[20])


def test_mass_index_validates_shapes() -> None:
    with pytest.raises(ValueError):
        mass_index(np.array([1.0]), np.array([1.0, 2.0]))
