import math
import numpy as np
import pytest

from app.core.indicators.tsi import tsi


def test_tsi_uptrend_pegs_positive() -> None:
    closes = np.linspace(100.0, 200.0, 80)
    out = tsi(closes, slow=25, fast=13)
    # Steady uptrend: TSI must be strongly positive at the tail
    assert out[-1] > 50.0


def test_tsi_downtrend_pegs_negative() -> None:
    closes = np.linspace(200.0, 100.0, 80)
    out = tsi(closes, slow=25, fast=13)
    assert out[-1] < -50.0


def test_tsi_in_reasonable_range() -> None:
    rng = np.random.default_rng(7)
    closes = 100.0 + np.cumsum(rng.normal(0, 1, 100))
    out = tsi(closes, slow=25, fast=13)
    valid = out[~np.isnan(out)]
    assert (valid >= -100).all() and (valid <= 100).all()


def test_tsi_leading_nan() -> None:
    closes = np.linspace(100.0, 200.0, 80)
    out = tsi(closes, slow=25, fast=13)
    # Diff-based, double-EMA-smoothed: many leading NaN
    assert math.isnan(out[0])


def test_tsi_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        tsi(np.array([1.0, 2.0, 3.0]), slow=0, fast=13)
