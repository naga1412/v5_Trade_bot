import math
import numpy as np
import pytest
import talib

from app.core.indicators.kama import kama


def test_kama_matches_talib_reference() -> None:
    rng = np.random.default_rng(42)
    closes = 100.0 + np.cumsum(rng.normal(0, 1, 80))
    out = kama(closes, period=10)
    expected = talib.KAMA(closes, timeperiod=10)
    # NaN-aware comparison
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_kama_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    out = kama(closes, period=10)
    # TA-Lib KAMA starts producing values after period bars
    assert math.isnan(out[0])
    assert math.isnan(out[5])


def test_kama_shape_matches_input() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    out = kama(closes, period=10)
    assert out.shape == closes.shape


def test_kama_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        kama(np.array([1.0, 2.0, 3.0]), period=0)
