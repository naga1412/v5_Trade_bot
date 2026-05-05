import math
import numpy as np
import pytest

from app.core.indicators.awesome_oscillator import awesome_oscillator


def test_ao_positive_on_clear_uptrend() -> None:
    n = 60
    base = np.linspace(100.0, 200.0, n)
    highs = base + 1.0
    lows = base - 1.0
    out = awesome_oscillator(highs, lows)
    assert out[-1] > 0.0


def test_ao_negative_on_clear_downtrend() -> None:
    n = 60
    base = np.linspace(200.0, 100.0, n)
    highs = base + 1.0
    lows = base - 1.0
    out = awesome_oscillator(highs, lows)
    assert out[-1] < 0.0


def test_ao_zero_on_flat_input() -> None:
    n = 60
    highs = np.full(n, 101.0)
    lows = np.full(n, 99.0)
    out = awesome_oscillator(highs, lows)
    valid = out[~np.isnan(out)]
    np.testing.assert_allclose(valid, 0.0, atol=1e-12)


def test_ao_leading_nan() -> None:
    n = 60
    base = np.linspace(100.0, 200.0, n)
    out = awesome_oscillator(base + 1.0, base - 1.0)
    for i in range(33):  # slow=34 → first valid idx 33
        assert math.isnan(out[i])


def test_ao_validates_shapes() -> None:
    with pytest.raises(ValueError):
        awesome_oscillator(np.array([1.0]), np.array([1.0, 2.0]))
