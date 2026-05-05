import math
import numpy as np
import pytest

from app.core.indicators.force_index import force_index


def test_force_index_positive_on_uptrend() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    vols = np.full(50, 1000.0)
    out = force_index(closes, vols, period=13)
    assert out[-1] > 0


def test_force_index_negative_on_downtrend() -> None:
    closes = np.linspace(200.0, 100.0, 50)
    vols = np.full(50, 1000.0)
    out = force_index(closes, vols, period=13)
    assert out[-1] < 0


def test_force_index_zero_on_flat_input() -> None:
    closes = np.full(40, 100.0)
    vols = np.full(40, 1000.0)
    out = force_index(closes, vols, period=13)
    valid = out[~np.isnan(out)]
    np.testing.assert_allclose(valid, 0.0, atol=1e-12)


def test_force_index_leading_nan() -> None:
    closes = np.linspace(100.0, 110.0, 30)
    vols = np.full(30, 1000.0)
    out = force_index(closes, vols, period=13)
    assert math.isnan(out[0])


def test_force_index_validates_shapes() -> None:
    with pytest.raises(ValueError):
        force_index(np.array([1.0]), np.array([1.0, 2.0]))
