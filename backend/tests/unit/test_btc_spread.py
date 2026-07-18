"""Unit tests for W3 BTC-spread feature computer.

Spec: docs/superpowers/specs/2026-07-18-brain-supervisor-expansion.md §3.4
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import app.core.features.btc_spread as btc_spread_mod
from app.core.features.btc_spread import (
    _MIN_BARS,
    compute,
    get_cached_btc_close,
    update_btc_close,
)


@pytest.fixture(autouse=True)
def reset_btc_cache():
    """Reset the module-level BTC cache before each test."""
    btc_spread_mod._BTC_CLOSE = None
    yield
    btc_spread_mod._BTC_CLOSE = None


def _make_bars(
    n: int,
    *,
    close_val: float = 100.0,
    spread: float = 1.0,
) -> pd.DataFrame:
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


# ── null paths ────────────────────────────────────────────────────────────────

def test_null_when_btc_unavailable():
    bars = _make_bars(_MIN_BARS)
    result = compute(bars)
    assert result == {"alt_btc_log_zscore": None}


def test_null_too_few_bars():
    update_btc_close(50000.0)
    bars = _make_bars(_MIN_BARS - 1)
    result = compute(bars)
    assert result == {"alt_btc_log_zscore": None}


def test_null_when_btc_close_zero():
    btc_spread_mod._BTC_CLOSE = 0.0
    bars = _make_bars(_MIN_BARS)
    result = compute(bars)
    assert result == {"alt_btc_log_zscore": None}


# ── cache operations ──────────────────────────────────────────────────────────

def test_update_get_roundtrip():
    assert get_cached_btc_close() is None
    update_btc_close(48000.0)
    assert get_cached_btc_close() == 48000.0


def test_update_overwrites_previous():
    update_btc_close(48000.0)
    update_btc_close(52000.0)
    assert get_cached_btc_close() == 52000.0


# ── z-score math ─────────────────────────────────────────────────────────────

def test_zero_when_alt_equals_btc():
    """Constant ratio (alt_close == btc_close for all bars) → z-score = 0."""
    btc_close = 50000.0
    update_btc_close(btc_close)
    bars = _make_bars(_MIN_BARS, close_val=btc_close)
    result = compute(bars)
    assert result["alt_btc_log_zscore"] == pytest.approx(0.0, abs=1e-9)


def test_positive_when_alt_outperforms():
    """Last bar above 30d average → positive z-score."""
    update_btc_close(50000.0)
    n = _MIN_BARS + 50
    # Rising trend: last bar is above the 720-bar window mean
    closes = np.linspace(100.0, 200.0, n)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    assert result["alt_btc_log_zscore"] is not None
    assert result["alt_btc_log_zscore"] > 0


def test_negative_when_alt_underperforms():
    """Last bar below 30d average → negative z-score."""
    update_btc_close(50000.0)
    n = _MIN_BARS + 50
    # Falling trend: last bar is below the 720-bar window mean
    closes = np.linspace(200.0, 100.0, n)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 1, "low": closes - 1,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    assert result["alt_btc_log_zscore"] is not None
    assert result["alt_btc_log_zscore"] < 0


def test_zscore_matches_manual_formula():
    """Compare compute() output against a direct numpy z-score on known data."""
    np.random.seed(42)
    btc_close = 50000.0
    update_btc_close(btc_close)
    n = _MIN_BARS + 100
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    bars = pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.ones(n),
    })
    result = compute(bars)
    window = closes[-_MIN_BARS:]
    ratios = np.log(np.maximum(window, 1e-12) / btc_close)
    expected_z = (ratios[-1] - np.mean(ratios)) / np.std(ratios, ddof=1)
    assert result["alt_btc_log_zscore"] == pytest.approx(float(expected_z), rel=1e-6)


def test_uses_only_last_720_bars():
    """Spike OUTSIDE the trailing 720-bar window must not change the z-score.

    Constructs two bar arrays with identical last-720 bars but different
    prefixes (one plain, one with an extreme spike). The z-score must be
    identical because compute() slices to [-720:] before computing.
    """
    np.random.seed(99)
    update_btc_close(50000.0)
    prefix_len = 50
    n = _MIN_BARS + prefix_len

    window = 100.0 + np.cumsum(np.random.randn(_MIN_BARS) * 0.5)
    plain_prefix = np.full(prefix_len, 100.0)
    spike_prefix = plain_prefix.copy()
    spike_prefix[0] = 1_000_000.0  # extreme spike in the discarded prefix

    for prefix in (plain_prefix, spike_prefix):
        closes = np.concatenate([prefix, window])
        bars = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": np.ones(n),
        })

    # Both should yield the same result since only the last 720 bars differ
    results = []
    for prefix in (plain_prefix, spike_prefix):
        closes = np.concatenate([prefix, window])
        bars = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": np.ones(n),
        })
        results.append(compute(bars))

    assert results[0] == results[1]


# ── return shape ─────────────────────────────────────────────────────────────

def test_return_keys_always_present():
    """compute() always returns exactly one key regardless of bar count."""
    for n in [5, _MIN_BARS - 1, _MIN_BARS, _MIN_BARS + 50]:
        result = compute(_make_bars(n))
        assert set(result.keys()) == {"alt_btc_log_zscore"}


def test_return_value_is_float_or_none():
    update_btc_close(50000.0)
    for n in [5, _MIN_BARS, _MIN_BARS + 50]:
        result = compute(_make_bars(n))
        v = result["alt_btc_log_zscore"]
        assert v is None or isinstance(v, float)
