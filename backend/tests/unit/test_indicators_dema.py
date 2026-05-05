import math
import numpy as np
import pytest

from app.core.indicators.dema import dema
from app.core.indicators.ema import ema


def test_dema_closer_to_last_close_than_ema_on_uptrend() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    d = dema(closes, period=10)
    e = ema(closes, period=10)
    # On a steady uptrend, DEMA should track the price more closely than EMA
    assert abs(d[-1] - closes[-1]) < abs(e[-1] - closes[-1])


def test_dema_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    d = dema(closes, period=10)
    # The DEMA needs 2*(period-1) bars before it produces a valid value
    for i in range(2 * (10 - 1)):
        assert math.isnan(d[i])


def test_dema_no_lookahead() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    full = dema(closes, period=10)
    truncated = dema(closes[:40], period=10)
    assert full[39] == pytest.approx(truncated[39])


def test_dema_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        dema(np.array([1.0, 2.0]), period=0)
