import math
import numpy as np
import pytest

from app.core.indicators.kvo import kvo


def test_kvo_returns_array_of_input_length() -> None:
    rng = np.random.default_rng(60)
    n = 100
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vols = rng.uniform(100, 1000, n)
    out = kvo(highs, lows, closes, vols)
    assert out.shape == closes.shape


def test_kvo_oscillates_around_zero() -> None:
    rng = np.random.default_rng(61)
    n = 200
    base = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vols = rng.uniform(100, 1000, n)
    out = kvo(highs, lows, closes, vols)
    valid = out[~np.isnan(out)]
    assert valid.size > 0
    # On a random walk we expect a mix of pos & neg values
    assert valid.min() < 0 and valid.max() > 0


def test_kvo_leading_nan() -> None:
    n = 80
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    closes = highs - 0.5
    vols = np.full(n, 1000.0)
    out = kvo(highs, lows, closes, vols)
    assert math.isnan(out[0])


def test_kvo_validates_shapes() -> None:
    with pytest.raises(ValueError):
        kvo(np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
