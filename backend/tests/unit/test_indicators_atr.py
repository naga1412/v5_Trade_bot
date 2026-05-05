import math
import numpy as np
import pytest
import talib

from app.core.indicators.atr import atr


def test_atr_matches_talib_reference() -> None:
    rng = np.random.default_rng(42)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    out = atr(highs, lows, closes, period=14)
    expected = talib.ATR(highs, lows, closes, timeperiod=14)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_atr_constant_range_equals_range() -> None:
    n = 30
    highs = np.full(n, 105.0)
    lows = np.full(n, 95.0)
    closes = np.full(n, 100.0)
    out = atr(highs, lows, closes, period=14)
    # TR = max(10, |105-100|, |95-100|) = 10 for all bars
    assert out[-1] == pytest.approx(10.0)


def test_atr_leading_nan() -> None:
    n = 30
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    closes = highs - 0.5
    out = atr(highs, lows, closes, period=14)
    assert math.isnan(out[0])
    assert math.isnan(out[12])


def test_atr_validates_shapes() -> None:
    with pytest.raises(ValueError):
        atr(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
