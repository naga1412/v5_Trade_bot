import math
import numpy as np
import pytest

from app.core.indicators.ema import ema
from app.core.indicators.macd import macd


def test_macd_returns_three_arrays() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    macd_line, signal_line, hist = macd(closes, fast=12, slow=26, signal=9)
    assert macd_line.shape == closes.shape
    assert signal_line.shape == closes.shape
    assert hist.shape == closes.shape


def test_macd_line_equals_fast_minus_slow_ema() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    macd_line, _, _ = macd(closes, fast=12, slow=26, signal=9)
    expected = ema(closes, 12) - ema(closes, 26)
    # NaN-aware comparison
    for i in range(len(closes)):
        if math.isnan(expected[i]):
            assert math.isnan(macd_line[i])
        else:
            assert macd_line[i] == pytest.approx(expected[i])


def test_macd_histogram_equals_macd_minus_signal() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    macd_line, signal_line, hist = macd(closes, fast=12, slow=26, signal=9)
    for i in range(len(closes)):
        if math.isnan(hist[i]):
            assert math.isnan(macd_line[i] - signal_line[i])
        else:
            assert hist[i] == pytest.approx(macd_line[i] - signal_line[i])


def test_macd_no_lookahead() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    full = macd(closes, 12, 26, 9)
    truncated = macd(closes[:50], 12, 26, 9)
    assert full[0][49] == pytest.approx(truncated[0][49])
    assert full[1][49] == pytest.approx(truncated[1][49])
