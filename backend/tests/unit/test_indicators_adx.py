import numpy as np
import pytest
import talib

from app.core.indicators.adx import adx


def test_adx_strong_uptrend_above_25() -> None:
    n = 80
    base = np.linspace(100.0, 200.0, n)  # strong, smooth uptrend
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    a, plus_di, minus_di = adx(highs, lows, closes, period=14)
    valid = a[~np.isnan(a)]
    # Strong steady uptrend → ADX should peg high
    assert valid[-1] > 25.0
    # In an uptrend, +DI should dominate -DI
    assert plus_di[-1] > minus_di[-1]


def test_adx_matches_talib_reference() -> None:
    rng = np.random.default_rng(70)
    n = 60
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    a, plus_di, minus_di = adx(highs, lows, closes, period=14)
    expected_a = talib.ADX(highs, lows, closes, timeperiod=14)
    expected_p = talib.PLUS_DI(highs, lows, closes, timeperiod=14)
    expected_m = talib.MINUS_DI(highs, lows, closes, timeperiod=14)
    np.testing.assert_allclose(a[~np.isnan(expected_a)], expected_a[~np.isnan(expected_a)], rtol=1e-10)
    np.testing.assert_allclose(plus_di[~np.isnan(expected_p)], expected_p[~np.isnan(expected_p)], rtol=1e-10)
    np.testing.assert_allclose(minus_di[~np.isnan(expected_m)], expected_m[~np.isnan(expected_m)], rtol=1e-10)


def test_adx_validates_shapes() -> None:
    with pytest.raises(ValueError):
        adx(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
