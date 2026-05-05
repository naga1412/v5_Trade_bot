import math
import numpy as np
import pytest

from app.core.indicators.vortex import vortex


def test_vortex_uptrend_vi_plus_above_minus() -> None:
    n = 40
    base = np.linspace(100.0, 200.0, n)
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vp, vm = vortex(highs, lows, closes, period=14)
    assert vp[-1] > vm[-1]


def test_vortex_downtrend_vi_minus_above_plus() -> None:
    n = 40
    base = np.linspace(200.0, 100.0, n)
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vp, vm = vortex(highs, lows, closes, period=14)
    assert vm[-1] > vp[-1]


def test_vortex_leading_nan() -> None:
    n = 30
    rng = np.random.default_rng(21)
    base = 100.0 + np.cumsum(rng.normal(0, 1, n))
    highs = base + 1.0
    lows = base - 1.0
    closes = base
    vp, vm = vortex(highs, lows, closes, period=14)
    for i in range(14):
        assert math.isnan(vp[i])
        assert math.isnan(vm[i])


def test_vortex_validates_shapes() -> None:
    with pytest.raises(ValueError):
        vortex(np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
