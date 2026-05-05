import math
import numpy as np
import pytest

from app.core.indicators.chaikin_money_flow import chaikin_money_flow


def test_cmf_in_range_minus1_to_1() -> None:
    rng = np.random.default_rng(50)
    n = 50
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base + rng.uniform(-0.9, 0.9, n)
    vols = rng.uniform(100, 1000, n)
    out = chaikin_money_flow(highs, lows, closes, vols)
    valid = out[~np.isnan(out)]
    assert (valid >= -1.0).all() and (valid <= 1.0).all()


def test_cmf_one_when_close_at_high_every_bar() -> None:
    n = 30
    highs = np.full(n, 110.0)
    lows = np.full(n, 100.0)
    closes = highs.copy()  # close at high → mfm = 1
    vols = np.full(n, 100.0)
    out = chaikin_money_flow(highs, lows, closes, vols, period=20)
    assert out[-1] == pytest.approx(1.0)


def test_cmf_minus_one_when_close_at_low_every_bar() -> None:
    n = 30
    highs = np.full(n, 110.0)
    lows = np.full(n, 100.0)
    closes = lows.copy()
    vols = np.full(n, 100.0)
    out = chaikin_money_flow(highs, lows, closes, vols, period=20)
    assert out[-1] == pytest.approx(-1.0)


def test_cmf_leading_nan() -> None:
    n = 25
    highs = np.linspace(100.0, 110.0, n)
    lows = highs - 1.0
    closes = highs - 0.5
    vols = np.full(n, 100.0)
    out = chaikin_money_flow(highs, lows, closes, vols, period=20)
    for i in range(19):
        assert math.isnan(out[i])
