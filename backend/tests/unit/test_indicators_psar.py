import numpy as np
import pytest
import talib

from app.core.indicators.psar import psar


def test_psar_matches_talib_reference() -> None:
    rng = np.random.default_rng(11)
    base = 100.0 + np.cumsum(rng.normal(0, 1, 80))
    highs = base + 0.5
    lows = base - 0.5
    out = psar(highs, lows)
    expected = talib.SAR(highs, lows, acceleration=0.02, maximum=0.2)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_psar_shape_matches_input() -> None:
    highs = np.linspace(100.0, 110.0, 30)
    lows = np.linspace(99.0, 109.0, 30)
    out = psar(highs, lows)
    assert out.shape == highs.shape


def test_psar_validates_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        psar(np.array([1.0, 2.0]), np.array([1.0]))


def test_psar_validates_positive_params() -> None:
    highs = np.array([1.0, 2.0])
    lows = np.array([0.5, 1.5])
    with pytest.raises(ValueError):
        psar(highs, lows, acceleration=0.0)
