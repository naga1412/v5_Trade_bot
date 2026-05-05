import numpy as np
import pytest
import talib

from app.core.indicators.mfi import mfi


def test_mfi_in_range_0_100() -> None:
    rng = np.random.default_rng(40)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vols = rng.uniform(100, 1000, n)
    out = mfi(highs, lows, closes, vols)
    valid = out[~np.isnan(out)]
    assert (valid >= 0).all() and (valid <= 100).all()


def test_mfi_matches_talib_reference() -> None:
    rng = np.random.default_rng(41)
    n = 40
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vols = rng.uniform(100, 1000, n)
    out = mfi(highs, lows, closes, vols, period=14)
    expected = talib.MFI(highs, lows, closes, vols, timeperiod=14)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_mfi_validates_shapes() -> None:
    with pytest.raises(ValueError):
        mfi(
            np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0])
        )
