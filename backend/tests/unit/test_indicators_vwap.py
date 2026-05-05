import numpy as np
import pytest

from app.core.indicators.vwap import vwap


def test_vwap_hand_computed_5_bars() -> None:
    highs = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    lows = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
    closes = np.array([9.5, 10.5, 11.5, 12.5, 13.5])
    vols = np.array([100.0, 200.0, 100.0, 100.0, 200.0])
    # typical = [9.5, 10.5, 11.5, 12.5, 13.5]
    # cum_pv = [950, 950+2100, +1150, +1250, +2700] = [950, 3050, 4200, 5450, 8150]
    # cum_v = [100, 300, 400, 500, 700]
    # vwap   = [9.5, 10.166..., 10.5, 10.9, 11.642857...]
    out = vwap(highs, lows, closes, vols)
    assert out[0] == pytest.approx(9.5)
    assert out[1] == pytest.approx(3050.0 / 300.0)
    assert out[4] == pytest.approx(8150.0 / 700.0)


def test_vwap_no_lookahead() -> None:
    rng = np.random.default_rng(3)
    n = 30
    highs = 100.0 + rng.uniform(0, 1, n)
    lows = 100.0 - rng.uniform(0, 1, n)
    closes = 100.0 + rng.normal(0, 0.5, n)
    vols = rng.uniform(50, 200, n)
    full = vwap(highs, lows, closes, vols)
    truncated = vwap(highs[:20], lows[:20], closes[:20], vols[:20])
    assert full[19] == pytest.approx(truncated[19])


def test_vwap_shape_validation() -> None:
    with pytest.raises(ValueError):
        vwap(np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([1.0, 2.0]))
