import numpy as np
import pytest

from app.core.indicators.stochastic import stochastic


def test_stochastic_returns_two_arrays_in_range_0_100() -> None:
    rng = np.random.default_rng(0)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    k, d = stochastic(highs, lows, closes)
    assert k.shape == d.shape == closes.shape
    valid_k = k[~np.isnan(k)]
    valid_d = d[~np.isnan(d)]
    assert (valid_k >= 0).all() and (valid_k <= 100).all()
    assert (valid_d >= 0).all() and (valid_d <= 100).all()


def test_stochastic_top_when_close_at_high() -> None:
    n = 40
    highs = np.full(n, 110.0)
    lows = np.full(n, 100.0)
    closes = np.full(n, 110.0)  # close at top of range every bar
    k, _ = stochastic(highs, lows, closes)
    assert k[-1] == pytest.approx(100.0)


def test_stochastic_bottom_when_close_at_low() -> None:
    n = 40
    highs = np.full(n, 110.0)
    lows = np.full(n, 100.0)
    closes = np.full(n, 100.0)  # close at bottom of range every bar
    k, _ = stochastic(highs, lows, closes)
    assert k[-1] == pytest.approx(0.0)


def test_stochastic_validates_shape() -> None:
    with pytest.raises(ValueError):
        stochastic(np.array([1.0]), np.array([1.0, 2.0]), np.array([1.0]))
