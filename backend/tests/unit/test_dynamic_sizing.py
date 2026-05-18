"""PR9 dynamic_sizing — pure-function tests.

Phase 2: classify_balance_tier. Phase 3: _resolve_p_win,
compute_kelly_fraction, compute_dynamic_size.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.trading.dynamic_sizing import (
    classify_balance_tier,
    compute_dynamic_size,
    compute_kelly_fraction,
    _resolve_p_win,
)


def _settings(
    *,
    enabled: bool = True,
    use_p_win: bool = True,
    fractional_kelly: float = 0.25,
    small_max: float = 1_000.0,
    medium_max: float = 10_000.0,
    large_max: float = 100_000.0,
):
    return SimpleNamespace(
        DYNAMIC_SIZING_ENABLED=enabled,
        SIZING_USE_P_WIN_WHEN_AVAILABLE=use_p_win,
        SIZING_FRACTIONAL_KELLY=fractional_kelly,
        SIZING_TIER_BOUNDARIES={
            "small_max": small_max,
            "medium_max": medium_max,
            "large_max": large_max,
        },
        SIZING_TIER_MAX_FRACTION={
            "small": 0.01,
            "medium": 0.02,
            "large": 0.05,
            "whale": 0.10,
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


# --- _resolve_p_win -----------------------------------------------------
# PR9 era: confidence proxy only. PR5 will land the real p_win integration
# (which requires async refactoring up the dispatcher path) — these tests
# pin the current behavior so PR5's refactor surfaces here first.


def test_resolve_p_win_uses_confidence_proxy() -> None:
    """PR9: always proxy. confidence_pct=70 → 0.7."""
    assert _resolve_p_win(70.0, _settings()) == 0.7


def test_resolve_p_win_proxy_with_flag_off() -> None:
    """Flag is a no-op until PR5 wires the real integration."""
    assert _resolve_p_win(50.0, _settings(use_p_win=False)) == 0.5


def test_resolve_p_win_clamps_to_unit_interval() -> None:
    assert _resolve_p_win(150.0, _settings()) == 1.0
    assert _resolve_p_win(-10.0, _settings()) == 0.0


# --- compute_kelly_fraction --------------------------------------------


def test_kelly_zero_when_no_edge() -> None:
    """p_win=0.5 → edge=0 → no bet."""
    assert compute_kelly_fraction(0.5, "whale", _settings()) == 0.0


def test_kelly_zero_when_negative_edge() -> None:
    """p_win<0.5 → negative edge → no bet (clamped to 0)."""
    assert compute_kelly_fraction(0.3, "whale", _settings()) == 0.0


def test_kelly_clamped_to_tier_small() -> None:
    """p_win=1.0, quarter-Kelly = 0.25, small-tier cap = 0.01 → 0.01."""
    assert compute_kelly_fraction(1.0, "small", _settings()) == 0.01


def test_kelly_clamped_to_tier_whale() -> None:
    """p_win=1.0, quarter-Kelly = 0.25, whale cap = 0.10 → 0.10."""
    assert compute_kelly_fraction(1.0, "whale", _settings()) == 0.10


def test_kelly_below_cap_returns_raw() -> None:
    """p_win=0.6 → edge=0.2 → quarter-Kelly=0.05. Whale cap=0.10 → 0.05."""
    result = compute_kelly_fraction(0.6, "whale", _settings())
    assert abs(result - 0.05) < 1e-9


def test_kelly_half_kelly_via_env_override() -> None:
    """Operator can flip SIZING_FRACTIONAL_KELLY=0.5 for half-Kelly."""
    settings = _settings(fractional_kelly=0.5)
    # p_win=0.7 → edge=0.4 → half-Kelly=0.2. Large-tier cap=0.05 → 0.05.
    assert compute_kelly_fraction(0.7, "large", settings) == 0.05


def test_kelly_eighth_kelly_via_env_override() -> None:
    """Eighth-Kelly is 2x more defensive than quarter."""
    settings = _settings(fractional_kelly=0.125)
    # p_win=0.7 → edge=0.4 → eighth-Kelly=0.05. Whale cap=0.10 → 0.05.
    result = compute_kelly_fraction(0.7, "whale", settings)
    assert abs(result - 0.05) < 1e-9


# --- compute_dynamic_size -----------------------------------------------


def test_dynamic_size_disabled_returns_none() -> None:
    """Default-OFF gate: caller falls back to legacy sizing."""
    result = compute_dynamic_size(
        balance_usdt=10_000.0, confidence_pct=70.0,
        settings=_settings(enabled=False),
    )
    assert result is None


def test_dynamic_size_small_tier_capped() -> None:
    """$500 user, 70% confidence:
    p_win=0.7 → edge=0.4 → quarter=0.1 → small-cap=0.01 → $5."""
    result = compute_dynamic_size(
        balance_usdt=500.0, confidence_pct=70.0, settings=_settings(),
    )
    assert result is not None
    assert abs(result - 5.0) < 1e-9


def test_dynamic_size_whale_tier_can_take_10pct() -> None:
    """$200k whale, 100% confidence: quarter=0.25 → whale-cap=0.10 → $20k."""
    result = compute_dynamic_size(
        balance_usdt=200_000.0, confidence_pct=100.0, settings=_settings(),
    )
    assert result is not None
    assert abs(result - 20_000.0) < 1e-9


def test_dynamic_size_no_edge_returns_zero() -> None:
    """Confidence at 50% → p_win=0.5 → no edge → 0 margin."""
    result = compute_dynamic_size(
        balance_usdt=10_000.0, confidence_pct=50.0, settings=_settings(),
    )
    assert result == 0.0


def test_dynamic_size_fails_open_on_internal_error() -> None:
    """Buggy compute → returns None so caller falls back to legacy."""
    bad_settings = _settings()
    # Make classify_balance_tier blow up by removing the boundary dict.
    bad_settings.SIZING_TIER_BOUNDARIES = None  # type: ignore[assignment]
    result = compute_dynamic_size(
        balance_usdt=1_000.0, confidence_pct=70.0, settings=bad_settings,
    )
    assert result is None
