import math
import numpy as np
import pytest

from app.core.indicators.sma import sma


def test_sma_last_value_matches_known() -> None:
    closes = np.arange(1.0, 11.0)  # [1..10]
    out = sma(closes, period=5)
    # Last 5 values: 6,7,8,9,10 -> mean = 8.0
    assert out[-1] == pytest.approx(8.0)


def test_sma_leading_nan_convention() -> None:
    closes = np.arange(1.0, 11.0)
    out = sma(closes, period=5)
    for i in range(4):
        assert math.isnan(out[i])
    assert out[4] == pytest.approx(3.0)  # mean of 1..5


def test_sma_no_lookahead() -> None:
    closes = np.arange(1.0, 21.0)
    full = sma(closes, period=5)
    truncated = sma(closes[:15], period=5)
    assert full[14] == pytest.approx(truncated[14])


def test_sma_period_longer_than_input_is_all_nan() -> None:
    out = sma(np.array([1.0, 2.0]), period=5)
    assert all(math.isnan(v) for v in out)


def test_sma_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        sma(np.array([1.0, 2.0]), period=0)
