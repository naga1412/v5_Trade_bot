import numpy as np
import pytest

from app.core.indicators.obv import obv


def test_obv_hand_computed_5_bars() -> None:
    closes = np.array([10.0, 11.0, 11.0, 10.5, 12.0])
    vols = np.array([100.0, 200.0, 50.0, 75.0, 300.0])
    out = obv(closes, vols)
    # OBV: 0, +200, 0 (flat), -75, +300
    # cumulative: 0, 200, 200, 125, 425
    np.testing.assert_allclose(out, np.array([0.0, 200.0, 200.0, 125.0, 425.0]))


def test_obv_uptrend_strictly_increasing() -> None:
    closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    vols = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    out = obv(closes, vols)
    # All up bars → OBV strictly rising
    diffs = np.diff(out)
    assert (diffs > 0).all()


def test_obv_validates_shape() -> None:
    with pytest.raises(ValueError):
        obv(np.array([1.0]), np.array([1.0, 2.0]))


def test_obv_no_lookahead() -> None:
    closes = np.array([10.0, 11.0, 10.5, 12.0, 11.5, 13.0])
    vols = np.array([100.0, 200.0, 50.0, 300.0, 75.0, 400.0])
    full = obv(closes, vols)
    truncated = obv(closes[:4], vols[:4])
    assert full[3] == pytest.approx(truncated[3])
