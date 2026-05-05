import numpy as np
import pytest
import talib

from app.core.indicators.cci import cci


def test_cci_matches_talib_reference() -> None:
    rng = np.random.default_rng(5)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    out = cci(highs, lows, closes, period=14)
    expected = talib.CCI(highs, lows, closes, timeperiod=14)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_cci_strong_uptrend_above_100() -> None:
    n = 30
    base = np.linspace(100.0, 200.0, n)
    highs = base + 0.5
    lows = base - 0.5
    closes = base
    out = cci(highs, lows, closes, period=14)
    # In a clear uptrend, CCI typically pegs > 100
    assert out[-1] > 100.0


def test_cci_validates_shapes() -> None:
    with pytest.raises(ValueError):
        cci(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
