"""Tests for cup_and_handle.py — Phase 2-B entry-timing fix.

Old behaviour: fired while handle was DECLINING (close < handle_start).
New behaviour: fires only when close BREAKS OUT ABOVE the cup rim.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.patterns.chart.cup_and_handle import CupAndHandlePattern


LOOKBACK = CupAndHandlePattern.LOOKBACK  # 100
_PATTERN = CupAndHandlePattern()

RIM = 100.0
BOTTOM = 88.0  # 12% cup depth, well above 5% minimum


def _make_bars(handle_closes: np.ndarray) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a valid cup + the supplied handle.

    Cup = concave-up quadratic from RIM → BOTTOM → RIM across 80 bars.
    Handle = caller-supplied 21 bars.
    Total = 101 bars; we score at current_idx=100.
    """
    cup_xs = np.arange(80, dtype=float)
    cup_closes = BOTTOM + (RIM - BOTTOM) * ((cup_xs / 79 * 2 - 1) ** 2)
    # cup_closes[0] == cup_closes[79] == RIM, minimum ≈ BOTTOM

    assert len(handle_closes) == 21, "handle must be exactly 21 bars"
    closes = np.concatenate([cup_closes, handle_closes])

    n = len(closes)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.ones(n) * 1_000_000,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1h"),
    )


def _handle(*, end_close: float) -> np.ndarray:
    """Handle that dips from RIM to RIM*0.98 then recovers to end_close."""
    h = np.array(
        [100.0, 99.5, 99.0, 98.5, 98.0, 98.0, 98.0, 98.5, 99.0, 99.5,
         100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
         end_close],
        dtype=float,
    )
    assert len(h) == 21
    return h


# --- pattern structure sanity ---

def test_pattern_id() -> None:
    assert _PATTERN.pattern_id == "cup_and_handle"


def test_pattern_type_is_chart() -> None:
    assert _PATTERN.pattern_type == "chart"


# --- regression: old behaviour fired during declining handle ---

def test_does_not_fire_during_declining_handle_below_rim() -> None:
    """Old code fired when close < handle_start. New code must not."""
    # Handle dips to 98 and end_close=99 — below rim, declining overall.
    bars = _make_bars(_handle(end_close=99.0))
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is None, (
        "should not fire while close (99) is still below the cup rim (100)"
    )


# --- new behaviour: fire at rim breakout ---

def test_fires_on_rim_breakout() -> None:
    """Close > rim is the entry trigger."""
    bars = _make_bars(_handle(end_close=101.0))
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is not None, "should fire when close breaks above the cup rim"
    assert result.direction == "LONG"
    assert result.pattern_id == "cup_and_handle"


def test_strength_is_above_base_on_clean_breakout() -> None:
    bars = _make_bars(_handle(end_close=101.0))
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is not None
    assert result.strength >= 0.70


def test_strength_increases_with_larger_breakout() -> None:
    bars_small = _make_bars(_handle(end_close=100.5))
    bars_large = _make_bars(_handle(end_close=102.0))
    r_small = _PATTERN.detect(bars_small, current_idx=100)
    r_large = _PATTERN.detect(bars_large, current_idx=100)
    assert r_small is not None and r_large is not None
    assert r_large.strength > r_small.strength


def test_confidence_elevated_on_breakout() -> None:
    bars = _make_bars(_handle(end_close=101.0))
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is not None
    assert result.confidence >= 0.70, (
        f"breakout confidence should be high, got {result.confidence}"
    )


def test_breakout_pct_in_evidence() -> None:
    bars = _make_bars(_handle(end_close=101.0))
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is not None
    assert "breakout_pct" in result.evidence
    assert result.evidence["breakout_pct"] > 0


# --- handle formation guard ---

def test_does_not_fire_when_handle_never_dips() -> None:
    """Handle that stays flat (never dips) is not a real consolidation."""
    flat_handle = np.ones(21) * RIM
    # Ends above rim to ensure it's not the close check blocking us.
    flat_handle[-1] = 101.0
    bars = _make_bars(flat_handle)
    result = _PATTERN.detect(bars, current_idx=100)
    assert result is None, "handle must dip below its start to qualify"


# --- not enough history ---

def test_returns_none_below_lookback() -> None:
    bars = _make_bars(_handle(end_close=101.0))
    result = _PATTERN.detect(bars, current_idx=50)
    assert result is None
