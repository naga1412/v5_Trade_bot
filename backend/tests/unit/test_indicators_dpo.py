import math
import numpy as np
import pytest

from app.core.indicators.dpo import dpo


def test_dpo_oscillates_near_zero_on_sinusoid() -> None:
    n = 80
    closes = 100.0 + 5.0 * np.sin(np.arange(n) * 2.0 * np.pi / 20.0)
    out = dpo(closes, period=20)
    valid = out[~np.isnan(out)]
    # Detrended sinusoid mean should be very close to zero
    assert abs(valid.mean()) < 0.5


def test_dpo_zero_on_flat_input() -> None:
    closes = np.full(40, 100.0)
    out = dpo(closes, period=10)
    valid = out[~np.isnan(out)]
    np.testing.assert_allclose(valid, 0.0, atol=1e-12)


def test_dpo_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 40)
    out = dpo(closes, period=10)
    for i in range(9):
        assert math.isnan(out[i])


def test_dpo_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        dpo(np.array([1.0, 2.0]), period=0)
