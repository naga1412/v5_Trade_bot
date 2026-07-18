"""Unit tests for W1 mean-reversion feature computer.

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.2
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.features.mean_reversion import _MIN_BARS, _atr14, _ema, compute


def _make_bars(n: int, *, close_val: float = 100.0, spread: float = 1.0) -> pd.DataFrame:
    """Synthetic OHLCV frame: flat price with configurable ATR spread."""
    closes = np.full(n, close_val)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + spread,
            "low": closes - spread,
            "close": closes,
            "volume": np.ones(n) * 1000.0,
        }
    )


# ── null path ─────────────────────────────────────────────────────────────────

def test_too_few_bars_returns_all_none():
    bars = _make_bars(_MIN_BARS - 1)
    result = compute(bars)
    assert result == {"z_ext": None, "bollinger_pct_b": None, "dist_7d_high_pct": None}


def test_exactly_min_bars_returns_values():
    bars = _make_bars(_MIN_BARS)
    result = compute(bars)
    # All three keys present and not None when ATR > 0.
    assert result["z_ext"] is not None
    assert result["bollinger_pct_b"] is not None
    assert result["dist_7d_high_pct"] is not None


# ── z_ext clamping ────────────────────────────────────────────────────────────

def test_z_ext_clamped_positive():
    """Price far above EMA → z_ext clamped at +5."""
    closes = np.linspace(100.0, 100.0, 30)
    closes[-1] = 200.0  # last bar way above EMA
    highs = closes + 1.0
    lows = closes - 1.0
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(30)})
    result = compute(bars)
    assert result["z_ext"] == pytest.approx(5.0)


def test_z_ext_clamped_negative():
    """Price far below EMA → z_ext clamped at -5."""
    closes = np.linspace(100.0, 100.0, 30)
    closes[-1] = 10.0
    highs = closes + 1.0
    lows = closes - 1.0
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(30)})
    result = compute(bars)
    assert result["z_ext"] == pytest.approx(-5.0)


def test_z_ext_zero_when_at_ema():
    """Flat price → EMA == last close → z_ext near 0."""
    bars = _make_bars(30, close_val=100.0, spread=1.0)
    result = compute(bars)
    # With flat price EMA20 == 100.0 == last close so numerator is 0.
    assert abs(result["z_ext"]) < 0.01


# ── bollinger_pct_b ───────────────────────────────────────────────────────────

def test_bollinger_at_midband():
    """Flat price → %B near 0.5 (price at midband)."""
    bars = _make_bars(30, close_val=100.0, spread=1.0)
    result = compute(bars)
    # Flat series: price == EMA == midband → pct_b ≈ 0.5
    assert result["bollinger_pct_b"] == pytest.approx(0.5, abs=0.05)


def test_bollinger_pct_b_clamped_upper():
    """Price well above upper band → clamped at 1.5."""
    closes = np.full(30, 100.0)
    closes[-1] = 300.0
    highs = closes + 0.01
    lows = closes - 0.01
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(30)})
    result = compute(bars)
    assert result["bollinger_pct_b"] == pytest.approx(1.5)


def test_bollinger_pct_b_clamped_lower():
    """Price well below lower band → clamped at -0.5."""
    closes = np.full(30, 100.0)
    closes[-1] = 0.001
    highs = closes + 0.01
    lows = closes - 0.01
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(30)})
    result = compute(bars)
    assert result["bollinger_pct_b"] == pytest.approx(-0.5)


# ── dist_7d_high_pct ──────────────────────────────────────────────────────────

def test_dist_7d_high_at_peak():
    """Last bar == 7d high → dist_7d_high_pct == 0."""
    bars = _make_bars(200, close_val=100.0, spread=0.5)
    result = compute(bars)
    # With flat price the last bar IS the 7d high, so dist ≈ 0.
    assert result["dist_7d_high_pct"] == pytest.approx(0.0, abs=0.01)


def test_dist_7d_high_declining():
    """Price steadily declining → last bar below 7d high → positive dist."""
    n = 200
    closes = np.linspace(150.0, 100.0, n)
    highs = closes + 1.0
    lows = closes - 1.0
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(n)})
    result = compute(bars)
    # 7d ≈ 168h lookback; high_7d > close → dist > 0
    assert result["dist_7d_high_pct"] > 0.0


def test_dist_7d_high_uses_at_most_168_bars():
    """The 7d window is capped at 168 bars regardless of total history."""
    n = 500
    closes = np.full(n, 100.0)
    # Spike at bar index 0 (very old) should NOT affect the 7d high.
    # The window covers only the last 168 bars.
    highs = closes.copy() + 1.0
    highs[0] = 10000.0  # far outside the 168-bar window
    lows = closes - 1.0
    bars = pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(n)})
    result = compute(bars)
    # 7d high should be 101 (closes + 1), not 10000
    assert result["dist_7d_high_pct"] == pytest.approx((101.0 - 100.0) / 100.0, abs=0.001)


# ── helper functions ──────────────────────────────────────────────────────────

def test_ema_converges_on_flat_series():
    data = np.full(100, 50.0)
    assert _ema(data, 20) == pytest.approx(50.0, rel=1e-6)


def test_atr14_flat_series():
    """For a flat series highs=101, lows=99, ATR14 ≈ 2.0."""
    n = 30
    closes = np.full(n, 100.0)
    highs = closes + 1.0
    lows = closes - 1.0
    val = _atr14(closes, highs, lows, period=14)
    assert val == pytest.approx(2.0, rel=0.01)


def test_atr14_too_short_returns_zero():
    closes = np.array([100.0, 101.0])
    assert _atr14(closes, closes + 1, closes - 1, period=14) == 0.0


# ── return shape ──────────────────────────────────────────────────────────────

def test_return_keys_always_present():
    """compute() always returns exactly the three keys."""
    for n in [5, _MIN_BARS, 100]:
        result = compute(_make_bars(n))
        assert set(result.keys()) == {"z_ext", "bollinger_pct_b", "dist_7d_high_pct"}


def test_return_values_are_float_or_none():
    """All values must be float or None (no numpy scalars)."""
    bars = _make_bars(50)
    result = compute(bars)
    for v in result.values():
        assert v is None or isinstance(v, float)
