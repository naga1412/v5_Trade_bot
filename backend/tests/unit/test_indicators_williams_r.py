import math
import numpy as np
import pytest

from app.core.indicators.williams_r import williams_r


def test_williams_r_in_range() -> None:
    rng = np.random.default_rng(2)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    out = williams_r(highs, lows, closes)
    valid = out[~np.isnan(out)]
    assert (valid <= 0).all() and (valid >= -100).all()


def test_williams_r_at_top_when_close_equals_highest_high() -> None:
    highs = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    lows = np.array([8.0, 9.0, 10.0, 11.0, 12.0, 13.0])
    closes = np.array([9.0, 10.0, 11.0, 12.0, 13.0, 15.0])  # last close = HH
    out = williams_r(highs, lows, closes, period=3)
    assert out[-1] == pytest.approx(0.0)


def test_williams_r_at_bottom_when_close_equals_lowest_low() -> None:
    # Lookback window of 3 ending at idx 5 → lows = [11, 12, 13]; LL = 11.
    highs = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    lows = np.array([8.0, 9.0, 10.0, 11.0, 12.0, 13.0])
    closes = np.array([9.0, 10.0, 11.0, 12.0, 13.0, 11.0])  # last close = LL of window
    out = williams_r(highs, lows, closes, period=3)
    assert out[-1] == pytest.approx(-100.0)


def test_williams_r_leading_nan() -> None:
    highs = np.linspace(10.0, 20.0, 10)
    lows = highs - 1.0
    closes = highs - 0.5
    out = williams_r(highs, lows, closes, period=5)
    for i in range(4):
        assert math.isnan(out[i])
