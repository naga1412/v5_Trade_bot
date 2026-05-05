"""Tests for shared chart-pattern helpers (Task D0)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.patterns.chart._helpers import (
    bar_body_size,
    bar_total_range,
    find_swing_highs,
    find_swing_lows,
    fit_trend_line,
    is_inside_bar,
    recent_atr,
)


def test_find_swing_highs_finds_three_clear_peaks() -> None:
    arr = np.array([1, 2, 5, 4, 3, 2, 6, 5, 4, 7, 6, 5], dtype=float)
    peaks = find_swing_highs(arr, prominence=0.5, distance=2)
    assert 2 in peaks
    assert 6 in peaks
    assert 9 in peaks


def test_find_swing_highs_returns_empty_on_monotonic() -> None:
    arr = np.linspace(1.0, 10.0, 50)
    assert find_swing_highs(arr) == []


def test_find_swing_lows_symmetric() -> None:
    arr = np.array([5, 4, 1, 2, 3, 4, 1, 2, 3, 0, 1, 2], dtype=float)
    troughs = find_swing_lows(arr, prominence=0.5, distance=2)
    assert 2 in troughs
    assert 6 in troughs
    assert 9 in troughs


def test_fit_trend_line_recovers_slope() -> None:
    xs = np.arange(10, dtype=float)
    ys = 2.0 * xs + 1.0
    slope, intercept = fit_trend_line(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_fit_trend_line_handles_degenerate_input() -> None:
    slope, intercept = fit_trend_line(np.array([5.0]), np.array([10.0]))
    assert slope == 0.0
    assert intercept == 10.0


def test_fit_trend_line_handles_empty_input() -> None:
    slope, intercept = fit_trend_line(np.array([]), np.array([]))
    assert slope == 0.0
    assert intercept == 0.0


def _make_bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_is_inside_bar() -> None:
    bars = pd.DataFrame(
        {
            "open": [100, 100, 100],
            "high": [105, 104, 103],
            "low": [95, 96, 97],
            "close": [102, 101, 100],
            "volume": [1, 1, 1],
        }
    )
    assert is_inside_bar(bars, 1) is True
    assert is_inside_bar(bars, 2) is True
    assert is_inside_bar(bars, 0) is False


def test_is_inside_bar_when_outside() -> None:
    bars = pd.DataFrame(
        {
            "open": [100, 100],
            "high": [105, 110],
            "low": [95, 90],
            "close": [102, 100],
            "volume": [1, 1],
        }
    )
    assert is_inside_bar(bars, 1) is False


def test_bar_body_size() -> None:
    bars = _make_bars([(100.0, 105.0, 95.0, 103.0, 1.0)])
    assert bar_body_size(bars, 0) == pytest.approx(3.0)


def test_bar_total_range() -> None:
    bars = _make_bars([(100.0, 105.0, 95.0, 103.0, 1.0)])
    assert bar_total_range(bars, 0) == pytest.approx(10.0)


def test_recent_atr_returns_zero_without_history() -> None:
    bars = _make_bars([(100.0, 101.0, 99.0, 100.0, 1.0)] * 5)
    assert recent_atr(bars, 4, period=14) == 0.0


def test_recent_atr_computes_mean_true_range() -> None:
    bars = _make_bars([(100.0, 102.0, 98.0, 100.0, 1.0)] * 30)
    atr = recent_atr(bars, 29, period=14)
    # All bars have range 4.0, no gaps, prev close == current close ⇒ TR = 4
    assert atr == pytest.approx(4.0)
