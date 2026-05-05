import numpy as np
import pytest
import talib

from app.core.indicators.ultimate import ultimate


def test_ultimate_in_range_0_100() -> None:
    rng = np.random.default_rng(8)
    n = 80
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    out = ultimate(highs, lows, closes)
    valid = out[~np.isnan(out)]
    assert (valid >= 0).all() and (valid <= 100).all()


def test_ultimate_matches_talib_reference() -> None:
    rng = np.random.default_rng(9)
    n = 60
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    out = ultimate(highs, lows, closes, 7, 14, 28)
    expected = talib.ULTOSC(highs, lows, closes, timeperiod1=7, timeperiod2=14, timeperiod3=28)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_ultimate_validates_shapes() -> None:
    with pytest.raises(ValueError):
        ultimate(np.array([1.0]), np.array([1.0, 2.0]), np.array([1.0]))
