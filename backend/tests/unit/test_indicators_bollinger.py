import numpy as np
import pytest

from app.core.indicators.bollinger import bollinger
from app.core.indicators.sma import sma


def test_bollinger_middle_equals_sma() -> None:
    rng = np.random.default_rng(1)
    closes = 100.0 + np.cumsum(rng.normal(0, 1, 50))
    upper, middle, lower = bollinger(closes, period=20, num_std=2.0)
    expected_mid = sma(closes, 20)
    valid = ~np.isnan(middle)
    np.testing.assert_allclose(middle[valid], expected_mid[valid], rtol=1e-10)
    # Width = upper - lower = 4 * stdev
    width = upper - lower
    valid_w = ~np.isnan(width)
    assert (width[valid_w] >= 0).all()


def test_bollinger_band_width_is_4_std() -> None:
    closes = np.linspace(100.0, 200.0, 40)
    upper, middle, lower = bollinger(closes, period=20, num_std=2.0)
    # Band width should equal 4 * sample std (TA-Lib uses N divisor)
    for i in range(19, 40):
        std = closes[i - 19 : i + 1].std()  # ddof=0
        assert (upper[i] - lower[i]) == pytest.approx(4.0 * std, rel=1e-9)


def test_bollinger_validates_period() -> None:
    with pytest.raises(ValueError):
        bollinger(np.array([1.0, 2.0]), period=0)


def test_bollinger_validates_num_std() -> None:
    with pytest.raises(ValueError):
        bollinger(np.array([1.0, 2.0, 3.0]), period=2, num_std=0.0)
