import numpy as np
import pytest
import talib

from app.core.indicators.trix import trix


def test_trix_matches_talib_reference() -> None:
    rng = np.random.default_rng(13)
    closes = 100.0 + np.cumsum(rng.normal(0, 1, 80))
    out = trix(closes, period=15)
    expected = talib.TRIX(closes, timeperiod=15)
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(out[valid], expected[valid], rtol=1e-10)


def test_trix_oscillates_near_zero_on_flat_input() -> None:
    closes = np.full(80, 100.0)
    out = trix(closes, period=10)
    valid = out[~np.isnan(out)]
    # Flat input → triple EMA is constant → TRIX ≈ 0
    np.testing.assert_allclose(valid, 0.0, atol=1e-9)


def test_trix_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        trix(np.array([1.0, 2.0]), period=0)
