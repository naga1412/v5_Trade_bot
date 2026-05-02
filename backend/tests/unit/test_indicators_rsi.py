import math
import numpy as np
import pytest

from app.core.indicators.rsi import rsi


# Wilder RSI fixture: classic TA textbook example
# closes from QuantInsti / Wilder original sample
WILDER_CLOSES = np.array([
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
])
EXPECTED_RSI_14_AT_INDEX_14 = 70.464  # ±0.01 tolerance vs published value


def test_rsi_matches_wilder_textbook_at_first_full_bar() -> None:
    out = rsi(WILDER_CLOSES, period=14)
    assert out[14] == pytest.approx(EXPECTED_RSI_14_AT_INDEX_14, abs=0.01)


def test_rsi_first_period_values_are_nan() -> None:
    out = rsi(WILDER_CLOSES, period=14)
    for i in range(14):
        assert math.isnan(out[i])


def test_rsi_all_gains_returns_100() -> None:
    closes = np.arange(1.0, 30.0)
    out = rsi(closes, period=14)
    assert out[-1] == pytest.approx(100.0)


def test_rsi_all_losses_returns_0() -> None:
    closes = np.arange(30.0, 1.0, -1.0)
    out = rsi(closes, period=14)
    assert out[-1] == pytest.approx(0.0)


def test_rsi_no_lookahead() -> None:
    full = rsi(WILDER_CLOSES, period=14)
    truncated = rsi(WILDER_CLOSES[:18], period=14)
    assert full[17] == pytest.approx(truncated[17])
