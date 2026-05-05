import math
import numpy as np
import pytest

from app.core.indicators.roc import roc


def test_roc_exact_value_on_linear_series() -> None:
    closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
    out = roc(closes, period=5)
    # roc[5] = 100 * (15 - 10)/10 = 50.0
    assert out[5] == pytest.approx(50.0)
    # roc[9] = 100 * (19 - 14)/14 = 35.7142857...
    assert out[9] == pytest.approx(100.0 * 5 / 14)


def test_roc_leading_nan() -> None:
    closes = np.arange(1.0, 11.0)
    out = roc(closes, period=3)
    for i in range(3):
        assert math.isnan(out[i])


def test_roc_period_too_long() -> None:
    out = roc(np.array([1.0, 2.0, 3.0]), period=5)
    assert all(math.isnan(v) for v in out)


def test_roc_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        roc(np.array([1.0, 2.0]), period=0)
