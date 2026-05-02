import math
import numpy as np
import pytest

from app.core.indicators.ema import ema


def test_ema_first_period_is_sma_then_recursive() -> None:
    closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    out = ema(closes, period=3)
    # First 2 values are NaN, value at index 2 = SMA(10,11,12) = 11.0
    assert math.isnan(out[0])
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(11.0)
    # Subsequent: alpha = 2/(3+1) = 0.5; ema[3] = 0.5*13 + 0.5*11 = 12.0
    assert out[3] == pytest.approx(12.0)
    assert out[4] == pytest.approx(13.0)
    assert out[5] == pytest.approx(14.0)


def test_ema_no_lookahead() -> None:
    closes = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    full = ema(closes, period=2)
    truncated = ema(closes[:4], period=2)
    # index 3 of full must equal index 3 of truncated — no future peek
    assert full[3] == pytest.approx(truncated[3])


def test_ema_period_longer_than_input_returns_all_nan() -> None:
    closes = np.array([1.0, 2.0])
    out = ema(closes, period=5)
    assert all(math.isnan(v) for v in out)


def test_ema_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ema(np.array([1.0, 2.0, 3.0]), period=0)
