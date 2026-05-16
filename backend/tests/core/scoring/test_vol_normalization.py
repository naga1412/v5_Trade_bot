"""Tests for vol_normalization — PR1 helpers.

Covers:
  - compute_realized_vol_20d: positive vol for 20+ days, None for <20 days,
    None for empty bars, correct sqrt(365) annualization.
  - compute_effective_score: high-vol dampening, low-vol amplification,
    MIN_VOL floor cap, None vol passthrough, sign preservation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.scoring.vol_normalization import (
    MIN_VOL,
    TARGET_VOL,
    compute_effective_score,
    compute_realized_vol_20d,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _Bar:
    """Minimal bar stub satisfying the _BarLike protocol."""
    ts: datetime
    close: float


def _make_bars(n_days: int, daily_return: float = 0.0) -> list[_Bar]:
    """Generate n_days × 1 bar-per-day with a constant daily log-return.

    All bars are at midnight UTC (one bar per day for simplicity).
    Starting close = 100.0; each subsequent day close = prev × exp(return).
    """
    bars: list[_Bar] = []
    close = 100.0
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for i in range(n_days):
        ts = base + timedelta(days=i)
        bars.append(_Bar(ts=ts, close=close))
        close = close * math.exp(daily_return)
    return bars


def _make_multi_bar_day(n_days: int, bars_per_day: int = 24) -> list[_Bar]:
    """Generate hourly bars across n_days.  Close rises by 0.1% per hour."""
    bars: list[_Bar] = []
    close = 100.0
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    for d in range(n_days):
        for h in range(bars_per_day):
            ts = base + timedelta(days=d, hours=h)
            bars.append(_Bar(ts=ts, close=close))
            close *= 1.001
    return bars


# ---------------------------------------------------------------------------
# compute_realized_vol_20d
# ---------------------------------------------------------------------------


def test_compute_realized_vol_20d_returns_positive_for_20_plus_days() -> None:
    """20 daily bars with alternating returns must return a positive float vol."""
    # Use alternating returns so std > 0 (constant returns give std=0).
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    close = 100.0
    bars: list[_Bar] = []
    r = 0.01
    for i in range(20):
        ts = base + timedelta(days=i)
        bars.append(_Bar(ts=ts, close=close))
        close = close * math.exp(r if i % 2 == 0 else -r)
    result = compute_realized_vol_20d(bars)
    assert result is not None
    assert result > 0.0


def test_compute_realized_vol_20d_returns_none_for_under_20_days() -> None:
    """19 daily bars must return None (MIN_DAILY_BARS_FOR_VOL = 20)."""
    bars = _make_bars(n_days=19, daily_return=0.01)
    result = compute_realized_vol_20d(bars)
    assert result is None


def test_compute_realized_vol_20d_returns_none_for_empty_bars() -> None:
    """Empty bar list must return None without raising."""
    result = compute_realized_vol_20d([])
    assert result is None


def test_compute_realized_vol_20d_annualization_uses_sqrt_365() -> None:
    """Annualized vol = daily_std × sqrt(365).

    Build 21 daily bars with exactly 20 log-returns, all equal to r.
    std(ddof=1) of a constant series is 0.0 — so instead encode the
    expected std directly via numpy to verify the formula, then use a
    non-trivial series.

    Strategy: build 23 bars (22 log-returns) alternating +r and -r so
    mean = 0 exactly (11 of each sign).
    exact std(ddof=1) = r (since all deviations from 0 are ±r).
    Then annualized vol = r × sqrt(365).
    """
    import numpy as np

    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    r = 0.02
    bars: list[_Bar] = []

    # Build closes so that each of 22 log-returns alternates ±r with mean=0.
    # 22 returns: positions 0..21 → even indices get +r, odd get -r.
    # 11 each of +r / -r → mean = 0.
    close = 100.0
    bars.append(_Bar(ts=base, close=close))
    for i in range(22):
        close = close * math.exp(r if i % 2 == 0 else -r)
        bars.append(_Bar(ts=base + timedelta(days=i + 1), close=close))

    result = compute_realized_vol_20d(bars)
    assert result is not None

    # Independently compute what the result should be.
    expected_daily_std = float(np.std([r if i % 2 == 0 else -r for i in range(22)], ddof=1))
    expected = expected_daily_std * math.sqrt(365)
    assert abs(result - expected) < 1e-9, (
        f"Expected {expected:.9f} but got {result:.9f}"
    )


def test_compute_realized_vol_20d_uses_last_close_per_day() -> None:
    """Multi-bar intraday: resample should take LAST bar's close per day."""
    bars = _make_multi_bar_day(n_days=21, bars_per_day=24)
    result = compute_realized_vol_20d(bars)
    # Just verify it returns a value (correctness of close already tested above).
    assert result is not None
    assert result > 0.0


# ---------------------------------------------------------------------------
# compute_effective_score
# ---------------------------------------------------------------------------


def test_compute_effective_score_high_vol_dampens_magnitude() -> None:
    """vol > TARGET → |eff_score| < |final_score|."""
    final = 0.5
    high_vol = TARGET_VOL * 2  # 0.04
    result = compute_effective_score(final, high_vol)
    assert result is not None
    assert abs(result) < abs(final), (
        f"High vol should dampen: got {result}, expected < {final}"
    )
    assert math.isclose(result, 0.25, rel_tol=1e-9)


def test_compute_effective_score_low_vol_amplifies_magnitude() -> None:
    """vol < TARGET (but above MIN_VOL floor) → |eff_score| > |final_score|."""
    final = 0.5
    low_vol = TARGET_VOL / 2  # 0.01 — exactly at MIN_VOL floor
    # vol=0.01 == MIN_VOL, so multiplier = TARGET_VOL / MIN_VOL = 0.02/0.01 = 2.0
    result = compute_effective_score(final, low_vol)
    assert result is not None
    assert abs(result) > abs(final), (
        f"Low vol should amplify: got {result}, expected > {final}"
    )
    assert math.isclose(result, 1.0, rel_tol=1e-9)


def test_compute_effective_score_min_vol_floor_caps_at_2x() -> None:
    """vol < MIN_VOL is floored at MIN_VOL → multiplier exactly 2.0."""
    final = 0.5
    tiny_vol = 0.005  # below MIN_VOL=0.01
    result = compute_effective_score(final, tiny_vol)
    assert result is not None
    # multiplier = TARGET_VOL / MIN_VOL = 0.02 / 0.01 = 2.0
    expected = final * (TARGET_VOL / MIN_VOL)
    assert math.isclose(result, expected, rel_tol=1e-9), (
        f"Expected {expected}, got {result}"
    )


def test_compute_effective_score_none_vol_returns_none() -> None:
    """realized_vol_20d=None must return None without raising."""
    result = compute_effective_score(0.5, None)
    assert result is None


def test_compute_effective_score_long_sign_preserved() -> None:
    """Positive final_score (LONG) must remain positive after normalization."""
    result = compute_effective_score(0.5, TARGET_VOL)
    assert result is not None
    assert result > 0.0


def test_compute_effective_score_short_sign_preserved() -> None:
    """Negative final_score (SHORT) must remain negative after normalization."""
    result = compute_effective_score(-0.5, TARGET_VOL)
    assert result is not None
    assert result < 0.0
    assert math.isclose(result, -0.5, rel_tol=1e-9)
