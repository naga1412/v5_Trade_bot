"""Unit tests for W2 volatility-state feature computer.

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.3
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.features.volatility_state import (
    _MIN_BARS_ATR_RATIO,
    _MIN_BARS_PERCENTILE,
    _MIN_BARS_REALIZED,
    _atr14,
    compute,
)


def _make_bars(
    n: int,
    *,
    close_val: float = 100.0,
    spread: float = 1.0,
    trend: float = 0.0,
) -> pd.DataFrame:
    """Synthetic OHLCV frame.

    trend > 0 produces linearly rising closes (useful for atr_expansion tests).
    """
    closes = np.linspace(close_val, close_val + trend * n, n)
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
    bars = _make_bars(_MIN_BARS_REALIZED - 1)
    result = compute(bars)
    assert result == {
        "realized_vol_24bar": None,
        "vol_percentile_30d": None,
        "atr_expansion_ratio": None,
    }


def test_exactly_min_bars_returns_realized_vol():
    bars = _make_bars(_MIN_BARS_REALIZED)
    result = compute(bars)
    assert result["realized_vol_24bar"] is not None
    assert result["vol_percentile_30d"] is None  # need 744+
    assert result["atr_expansion_ratio"] is None  # need 35+


# ── realized_vol_24bar ────────────────────────────────────────────────────────

def test_realized_vol_formula_known_returns():
    """Construct bars with known log-returns → verify annualized vol formula."""
    n = 50
    # Log-return = 0.01 for every step → σ(log_returns[-24:]) = 0 (constant returns)
    # so realized_vol_24bar should be 0
    step = np.exp(0.01) - 1  # each close = prev * exp(0.01)
    closes = np.cumprod(np.ones(n) * (1 + step)) * 100.0
    # Constant returns → std == 0
    bars = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    assert result["realized_vol_24bar"] == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_matches_manual_formula():
    """Compare compute() output against numpy reference on random-ish data."""
    np.random.seed(42)
    n = 100
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    log_ret = np.diff(np.log(closes))[-24:]
    expected = float(np.std(log_ret, ddof=1)) * np.sqrt(8760.0)
    assert result["realized_vol_24bar"] == pytest.approx(expected, rel=1e-6)


def test_realized_vol_positive():
    """Random walk prices → positive realized vol."""
    np.random.seed(7)
    closes = 100 + np.cumsum(np.random.randn(50) * 1.0)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": np.ones(50),
    })
    result = compute(bars)
    assert result["realized_vol_24bar"] > 0


# ── vol_percentile_30d ────────────────────────────────────────────────────────

def test_vol_percentile_none_below_744_bars():
    bars = _make_bars(743)
    result = compute(bars)
    assert result["vol_percentile_30d"] is None


def test_vol_percentile_in_0_1():
    """With 744 bars available, percentile must be in [0, 1]."""
    np.random.seed(123)
    n = 800
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    if result["vol_percentile_30d"] is not None:
        assert 0.0 <= result["vol_percentile_30d"] <= 1.0


def test_vol_percentile_high_when_current_vol_is_max():
    """If the last 24 bars have extremely high vol, percentile should be near 1."""
    np.random.seed(0)
    n = 800
    # Calm for first 776 bars, then wild for last 24
    closes = np.ones(n) * 100.0
    closes[776:] = 100.0 + np.cumsum(np.random.randn(24) * 10.0)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 0.1, "low": closes - 0.1,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    if result["vol_percentile_30d"] is not None:
        assert result["vol_percentile_30d"] > 0.9


# ── atr_expansion_ratio ───────────────────────────────────────────────────────

def test_atr_expansion_none_below_35_bars():
    bars = _make_bars(34)
    result = compute(bars)
    assert result["atr_expansion_ratio"] is None


def test_atr_expansion_gt_1_when_expanding():
    """Rising spread in recent bars → atr_expansion_ratio > 1."""
    n = 50
    closes = np.ones(n) * 100.0
    # Narrow spread early, wide spread at end
    highs = np.concatenate([closes[:30] + 0.5, closes[30:] + 5.0])
    lows = np.concatenate([closes[:30] - 0.5, closes[30:] - 5.0])
    bars = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    assert result["atr_expansion_ratio"] is not None
    assert result["atr_expansion_ratio"] > 1.0


def test_atr_expansion_lt_1_when_contracting():
    """Wide spread early, narrow spread at end → ratio < 1."""
    n = 50
    closes = np.ones(n) * 100.0
    highs = np.concatenate([closes[:30] + 5.0, closes[30:] + 0.5])
    lows = np.concatenate([closes[:30] - 5.0, closes[30:] - 0.5])
    bars = pd.DataFrame({
        "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    assert result["atr_expansion_ratio"] is not None
    assert result["atr_expansion_ratio"] < 1.0


def test_atr_expansion_near_1_on_flat_series():
    """Constant spread → ratio near 1.0."""
    bars = _make_bars(50, spread=2.0)
    result = compute(bars)
    assert result["atr_expansion_ratio"] == pytest.approx(1.0, abs=0.05)


# ── atr14 helper ─────────────────────────────────────────────────────────────

def test_atr14_too_short_returns_zero():
    closes = np.array([100.0])
    assert _atr14(closes, closes + 1, closes - 1) == 0.0


def test_atr14_flat_series():
    n = 20
    closes = np.full(n, 100.0)
    val = _atr14(closes, closes + 2.0, closes - 2.0)
    assert val == pytest.approx(4.0, rel=0.01)


# ── return shape ─────────────────────────────────────────────────────────────

def test_return_keys_always_present():
    """compute() always returns exactly three keys regardless of bar count."""
    for n in [5, 25, 35, 100, 750]:
        result = compute(_make_bars(n))
        assert set(result.keys()) == {"realized_vol_24bar", "vol_percentile_30d", "atr_expansion_ratio"}


def test_return_values_are_float_or_none():
    bars = _make_bars(800)
    result = compute(bars)
    for v in result.values():
        assert v is None or isinstance(v, float)
