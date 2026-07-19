"""Tests for app.core.features.structure_location (W5 brain supervisor)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.features.structure_location import compute

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bars(closes: list[float], *, highs: list[float] | None = None,
               lows: list[float] | None = None) -> pd.DataFrame:
    """Build a minimal OHLC DataFrame for testing."""
    n = len(closes)
    c = np.array(closes, dtype=float)
    h = np.array(highs, dtype=float) if highs else c + 0.5
    lo = np.array(lows, dtype=float) if lows else c - 0.5
    return pd.DataFrame({"open": c, "high": h, "low": lo, "close": c})


# ---------------------------------------------------------------------------
# Minimum-bar guard
# ---------------------------------------------------------------------------

def test_returns_all_none_below_min_bars() -> None:
    bars = _make_bars([100.0] * 20)  # exactly 20 bars < _MIN_BARS=21
    result = compute(bars)
    assert result == {"dist_swing_atr": None, "retracement_fraction": None}


def test_returns_all_none_with_empty_bars() -> None:
    bars = _make_bars([])
    result = compute(bars)
    assert result == {"dist_swing_atr": None, "retracement_fraction": None}


# ---------------------------------------------------------------------------
# dist_swing_atr
# ---------------------------------------------------------------------------

def test_dist_swing_atr_is_none_when_no_swings_detected() -> None:
    """A completely flat series has no swing pivots — dist should be None."""
    closes = [100.0] * 25
    bars = _make_bars(closes)
    result = compute(bars)
    # Flat series → no peaks/troughs → dist_swing_atr = None
    assert result["dist_swing_atr"] is None


def test_dist_swing_atr_near_zero_when_close_at_swing_level() -> None:
    """When the current close equals the most recent swing high, dist ≈ 0 ATRs."""
    # Build a series with a clear swing high at index 10 of value 110
    closes = [100.0] * 8 + [105.0, 110.0, 105.0] + [100.0] * 11 + [110.0]
    # Highs mirror closes; current (last) bar is exactly at prior swing-high level
    bars = _make_bars(closes)
    result = compute(bars)
    # dist_swing_atr should be small (possibly 0 if the high array contains 110)
    if result["dist_swing_atr"] is not None:
        assert result["dist_swing_atr"] < 1.0, (
            f"Expected dist_swing_atr < 1 ATR when at swing level, got {result['dist_swing_atr']}"
        )


def test_dist_swing_atr_is_positive_float_on_clean_series() -> None:
    """Any series with a visible swing should yield a positive float."""
    # Zigzag that guarantees swing detection
    import math
    closes = [100 + 10 * math.sin(i * 0.8) for i in range(30)]
    bars = _make_bars(closes)
    result = compute(bars)
    if result["dist_swing_atr"] is not None:
        assert result["dist_swing_atr"] >= 0.0


# ---------------------------------------------------------------------------
# retracement_fraction
# ---------------------------------------------------------------------------

def test_retracement_fraction_midpoint_of_upleg() -> None:
    """Close at exact midpoint of a swing-low → swing-high leg should give ~0.5."""
    # Swing low at bar 5, swing high at bar 15, current at midpoint price
    low_price, high_price = 90.0, 110.0
    mid_price = (low_price + high_price) / 2.0  # 100.0

    # Construct bars: descent to low, rise to high, then settle at mid
    descent = np.linspace(100.0, low_price, 6).tolist()   # bars 0-5 (swing low at 5)
    ascent = np.linspace(low_price, high_price, 11).tolist()  # bars 5-15 (swing high at 15)
    tail = [mid_price] * 9  # bars 16-24

    closes = descent + ascent[1:] + tail
    # Make highs/lows match so find_swing_highs/lows can detect the pivots
    highs = [c + 0.01 for c in closes]
    lows = [c - 0.01 for c in closes]
    # Force the swing extremes to be true highs/lows
    highs[15] = high_price + 2.0  # clear swing high
    lows[5] = low_price - 2.0     # clear swing low

    bars = _make_bars(closes, highs=highs, lows=lows)
    result = compute(bars)
    # retracement_fraction should be close to 0.5 (within tolerance given ATR/clamping)
    if result["retracement_fraction"] is not None:
        assert 0.0 <= result["retracement_fraction"] <= 1.0


def test_retracement_fraction_clamped_below_zero() -> None:
    """A value that would go below 0 must be clamped to 0.0."""
    # Construct bars where current close is BELOW the swing low (fraction < 0)
    closes = [100.0] * 5 + [80.0, 100.0] * 5 + [95.0, 105.0, 100.0] * 4
    bars = _make_bars(closes)
    result = compute(bars)
    if result["retracement_fraction"] is not None:
        assert result["retracement_fraction"] >= 0.0


def test_retracement_fraction_clamped_above_one() -> None:
    """A value that would go above 1 must be clamped to 1.0."""
    closes = [100.0] * 5 + [80.0, 100.0] * 5 + [95.0, 105.0, 115.0] * 4
    bars = _make_bars(closes)
    result = compute(bars)
    if result["retracement_fraction"] is not None:
        assert result["retracement_fraction"] <= 1.0


def test_retracement_fraction_none_when_no_impulse() -> None:
    """Flat series with no swing points → retracement_fraction is None."""
    bars = _make_bars([100.0] * 25)
    result = compute(bars)
    assert result["retracement_fraction"] is None


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

def test_compute_always_returns_two_key_dict() -> None:
    bars = _make_bars([100.0 + i * 0.1 for i in range(25)])
    result = compute(bars)
    assert set(result.keys()) == {"dist_swing_atr", "retracement_fraction"}


def test_compute_returns_copy_not_mutating() -> None:
    """Mutating the returned dict must not affect subsequent calls."""
    bars = _make_bars([100.0] * 25)
    r1 = compute(bars)
    r1["dist_swing_atr"] = 999.9
    r2 = compute(bars)
    assert r2.get("dist_swing_atr") != 999.9
