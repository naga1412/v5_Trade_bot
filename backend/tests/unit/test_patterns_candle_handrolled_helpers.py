"""Tests for shared hand-rolled candle pattern utilities.

Cover the geometric helpers (body/wick), trend helpers (swing high/low),
and indicator wrappers (ATR, RSI) defined in
``app.core.patterns.candle._helpers``.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.patterns.candle._helpers import (
    body_size,
    compute_atr,
    compute_rsi_at,
    is_bearish,
    is_bullish,
    lower_wick,
    recent_swing_high,
    recent_swing_low,
    upper_wick,
)


def _bar(o: float, h: float, low: float, c: float, v: float = 1_000.0) -> pd.Series:
    return pd.Series({"open": o, "high": h, "low": low, "close": c, "volume": v})


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_body_size_is_abs_close_minus_open() -> None:
    assert body_size(_bar(100.0, 105.0, 99.0, 103.0)) == pytest.approx(3.0)
    assert body_size(_bar(100.0, 105.0, 99.0, 97.0)) == pytest.approx(3.0)


def test_upper_wick_is_high_minus_max_oc() -> None:
    assert upper_wick(_bar(100.0, 105.0, 99.0, 103.0)) == pytest.approx(2.0)
    # Bearish bar: max(o,c) = open = 100
    assert upper_wick(_bar(100.0, 105.0, 95.0, 96.0)) == pytest.approx(5.0)


def test_lower_wick_is_min_oc_minus_low() -> None:
    assert lower_wick(_bar(100.0, 105.0, 99.0, 103.0)) == pytest.approx(1.0)
    assert lower_wick(_bar(100.0, 105.0, 95.0, 96.0)) == pytest.approx(1.0)


def test_is_bullish_and_is_bearish_strict_inequality() -> None:
    assert is_bullish(_bar(100.0, 105.0, 99.0, 103.0))
    assert not is_bullish(_bar(100.0, 105.0, 99.0, 100.0))
    assert is_bearish(_bar(100.0, 105.0, 95.0, 96.0))
    assert not is_bearish(_bar(100.0, 105.0, 95.0, 100.0))


def test_recent_swing_high_returns_max_high_in_lookback_window() -> None:
    bars = _bars(
        [(100.0, 100.0 + i, 99.0, 100.0, 1_000.0) for i in range(25)]
    )
    # Last 20 bars: highs from 105.0 (idx=5) to 124.0 (idx=24).
    assert recent_swing_high(bars, lookback=20) == pytest.approx(124.0)


def test_recent_swing_low_returns_min_low_in_lookback_window() -> None:
    bars = _bars(
        [(100.0, 101.0, 100.0 - i, 100.0, 1_000.0) for i in range(25)]
    )
    assert recent_swing_low(bars, lookback=20) == pytest.approx(100.0 - 24)


def test_recent_swing_high_handles_short_history() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 5)
    # Only 5 bars but lookback=20 — should return the max of available.
    assert recent_swing_high(bars, lookback=20) == pytest.approx(101.0)


def test_compute_atr_returns_zero_when_history_short() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 5)
    assert compute_atr(bars, current_idx=4, period=14) == 0.0


def test_compute_atr_positive_for_volatile_bars() -> None:
    bars = _bars(
        [(100.0 + i, 102.0 + i, 98.0 + i, 100.0 + i, 1_000.0) for i in range(30)]
    )
    val = compute_atr(bars, current_idx=29, period=14)
    assert val > 0.0


def test_compute_rsi_at_handles_short_history() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 5)
    val = compute_rsi_at(bars, current_idx=4, period=14)
    # Cannot compute RSI yet — returns NaN (caller treats as missing).
    assert val != val  # NaN check


def test_compute_rsi_at_in_uptrend_is_high() -> None:
    bars = _bars(
        [(100.0 + i, 101.0 + i, 99.5 + i, 100.5 + i, 1_000.0) for i in range(40)]
    )
    val = compute_rsi_at(bars, current_idx=39, period=14)
    assert val > 70.0


def test_compute_rsi_at_in_downtrend_is_low() -> None:
    bars = _bars(
        [(100.0 - i, 101.0 - i, 99.5 - i, 99.5 - i, 1_000.0) for i in range(40)]
    )
    val = compute_rsi_at(bars, current_idx=39, period=14)
    assert val < 30.0
