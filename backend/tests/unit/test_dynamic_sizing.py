"""PR9 dynamic_sizing — pure-function tests.

Phase 2: covers classify_balance_tier. Phase 3 will extend this file
with compute_kelly_fraction + compute_dynamic_size + _resolve_p_win
tests.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.trading.dynamic_sizing import classify_balance_tier


def _settings(
    small_max: float = 1_000.0,
    medium_max: float = 10_000.0,
    large_max: float = 100_000.0,
):
    return SimpleNamespace(
        SIZING_TIER_BOUNDARIES={
            "small_max": small_max,
            "medium_max": medium_max,
            "large_max": large_max,
        },
    )


def test_classify_small_below_1k() -> None:
    assert classify_balance_tier(500.0, _settings()) == "small"


def test_classify_small_at_zero() -> None:
    assert classify_balance_tier(0.0, _settings()) == "small"


def test_classify_small_negative_is_safe_baseline() -> None:
    """Negative balance shouldn't happen but maps to most-conservative tier."""
    assert classify_balance_tier(-100.0, _settings()) == "small"


def test_classify_small_just_under_1k() -> None:
    assert classify_balance_tier(999.99, _settings()) == "small"


def test_classify_medium_at_1k_boundary() -> None:
    """Boundary: balance == small_max → medium."""
    assert classify_balance_tier(1_000.0, _settings()) == "medium"


def test_classify_medium_just_under_10k() -> None:
    assert classify_balance_tier(9_999.99, _settings()) == "medium"


def test_classify_large_at_10k_boundary() -> None:
    assert classify_balance_tier(10_000.0, _settings()) == "large"


def test_classify_large_just_under_100k() -> None:
    assert classify_balance_tier(99_999.99, _settings()) == "large"


def test_classify_whale_at_100k_boundary() -> None:
    assert classify_balance_tier(100_000.0, _settings()) == "whale"


def test_classify_whale_way_above_100k() -> None:
    assert classify_balance_tier(5_000_000.0, _settings()) == "whale"


def test_classify_respects_operator_override_boundaries() -> None:
    """Operator can shift the boundaries via env. Verify classifier honors."""
    custom = _settings(small_max=500.0, medium_max=5_000.0, large_max=50_000.0)
    assert classify_balance_tier(400.0, custom) == "small"
    assert classify_balance_tier(600.0, custom) == "medium"
    assert classify_balance_tier(6_000.0, custom) == "large"
    assert classify_balance_tier(60_000.0, custom) == "whale"
