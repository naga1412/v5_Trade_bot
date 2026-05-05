import math
import numpy as np
import pytest

from app.core.indicators.ichimoku import ichimoku


def _ohlc(n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    base = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    return highs, lows, closes


def test_ichimoku_returns_five_arrays_with_input_shape() -> None:
    h, low, c = _ohlc(80)
    tenkan, kijun, sa, sb, chikou = ichimoku(h, low, c)
    for arr in (tenkan, kijun, sa, sb, chikou):
        assert arr.shape == c.shape


def test_ichimoku_tenkan_is_rolling_midpoint() -> None:
    h, low, c = _ohlc(60)
    tenkan, _, _, _, _ = ichimoku(h, low, c, tenkan_period=9)
    # First 8 values are NaN
    for i in range(8):
        assert math.isnan(tenkan[i])
    # At index 8: (max(h[0..8]) + min(l[0..8])) / 2
    expected = (h[:9].max() + low[:9].min()) / 2.0
    assert tenkan[8] == pytest.approx(expected)


def test_ichimoku_chikou_is_close_shifted_back() -> None:
    h, low, c = _ohlc(60)
    _, _, _, _, chikou = ichimoku(h, low, c, displacement=26)
    # chikou[i] = close[i + 26]
    assert chikou[0] == pytest.approx(c[26])
    assert chikou[33] == pytest.approx(c[59])
    # Tail must be NaN (no future data)
    assert math.isnan(chikou[59])


def test_ichimoku_validates_shapes() -> None:
    with pytest.raises(ValueError):
        ichimoku(np.array([1.0]), np.array([1.0, 2.0]), np.array([1.0]))
