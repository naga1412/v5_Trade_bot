"""PR9 dynamic_sizing — pure-function tests.

Phase 2: classify_balance_tier. Phase 3: _resolve_p_win,
compute_kelly_fraction, compute_dynamic_size.

_resolve_p_win and compute_dynamic_size are async as of the 2026-08-14
remediation work order A2 fix -- see dynamic_sizing.py's module
docstring.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.scoring.types import Direction
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
# 2026-08-14 remediation work order A2: SIZING_USE_P_WIN_WHEN_AVAILABLE
# was read into a throwaway variable and never branched on -- Kelly
# sizing always used the confidence proxy regardless of the flag or
# whether a calibrated model was available. Fixed: the flag now
# actually gates a call to the real predict_p_win when final_score +
# direction are supplied; without them (or with the flag off, or when
# the model itself returns None) it's the same proxy behavior as before.


@pytest.mark.asyncio
async def test_resolve_p_win_uses_confidence_proxy_when_no_signal_supplied() -> None:
    """No final_score/direction -> always proxy, regardless of the flag.
    confidence_pct=70 -> 0.7."""
    assert await _resolve_p_win(70.0, _settings()) == 0.7


@pytest.mark.asyncio
async def test_resolve_p_win_proxy_with_flag_off_even_with_signal() -> None:
    """Flag off -> proxy even when a real signal IS available."""
    with patch(
        "app.core.scoring.p_win_calibrator.predict_p_win",
        new=AsyncMock(return_value=0.99),
    ) as mock_predict:
        result = await _resolve_p_win(
            50.0, _settings(use_p_win=False),
            final_score=0.8, direction=Direction.LONG,
        )
    assert result == 0.5
    mock_predict.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_p_win_clamps_to_unit_interval() -> None:
    assert await _resolve_p_win(150.0, _settings()) == 1.0
    assert await _resolve_p_win(-10.0, _settings()) == 0.0


@pytest.mark.asyncio
async def test_resolve_p_win_uses_calibrated_model_when_flag_on_and_signal_present() -> None:
    """The actual A2 fix: flag on + real signal -> calls predict_p_win
    and uses its result instead of the confidence proxy."""
    with patch(
        "app.core.scoring.p_win_calibrator.predict_p_win",
        new=AsyncMock(return_value=0.63),
    ) as mock_predict:
        result = await _resolve_p_win(
            70.0, _settings(use_p_win=True),
            final_score=0.42, direction=Direction.LONG,
        )
    assert result == 0.63  # calibrated value, NOT confidence_pct/100 (0.7)
    mock_predict.assert_awaited_once_with(0.42, Direction.LONG)


@pytest.mark.asyncio
async def test_resolve_p_win_falls_back_to_proxy_when_model_abstains() -> None:
    """predict_p_win returning None (NEUTRAL direction, no fitted model,
    calibration failure) falls through to the proxy -- same fail-open
    contract as before the fix."""
    with patch(
        "app.core.scoring.p_win_calibrator.predict_p_win",
        new=AsyncMock(return_value=None),
    ):
        result = await _resolve_p_win(
            70.0, _settings(use_p_win=True),
            final_score=0.42, direction=Direction.LONG,
        )
    assert result == 0.7


@pytest.mark.asyncio
async def test_resolve_p_win_accepts_plain_string_direction() -> None:
    """dispatcher.py's SignalProposal.direction is a plain string
    Literal["LONG","SHORT"], not the Direction enum -- must coerce."""
    with patch(
        "app.core.scoring.p_win_calibrator.predict_p_win",
        new=AsyncMock(return_value=0.55),
    ) as mock_predict:
        result = await _resolve_p_win(
            70.0, _settings(use_p_win=True),
            final_score=0.3, direction="SHORT",
        )
    assert result == 0.55
    mock_predict.assert_awaited_once_with(0.3, Direction.SHORT)


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


@pytest.mark.asyncio
async def test_dynamic_size_disabled_returns_none() -> None:
    """Default-OFF gate: caller falls back to legacy sizing."""
    result = await compute_dynamic_size(
        balance_usdt=10_000.0, confidence_pct=70.0,
        settings=_settings(enabled=False),
    )
    assert result is None


@pytest.mark.asyncio
async def test_dynamic_size_small_tier_capped() -> None:
    """$500 user, 70% confidence, no real signal supplied (proxy path):
    p_win=0.7 → edge=0.4 → quarter=0.1 → small-cap=0.01 → $5."""
    result = await compute_dynamic_size(
        balance_usdt=500.0, confidence_pct=70.0, settings=_settings(),
    )
    assert result is not None
    assert abs(result - 5.0) < 1e-9


@pytest.mark.asyncio
async def test_dynamic_size_whale_tier_can_take_10pct() -> None:
    """$200k whale, 100% confidence, proxy path: quarter=0.25 → whale-cap=0.10 → $20k."""
    result = await compute_dynamic_size(
        balance_usdt=200_000.0, confidence_pct=100.0, settings=_settings(),
    )
    assert result is not None
    assert abs(result - 20_000.0) < 1e-9


@pytest.mark.asyncio
async def test_dynamic_size_no_edge_returns_zero() -> None:
    """Confidence at 50% → p_win=0.5 → no edge → 0 margin."""
    result = await compute_dynamic_size(
        balance_usdt=10_000.0, confidence_pct=50.0, settings=_settings(),
    )
    assert result == 0.0


@pytest.mark.asyncio
async def test_dynamic_size_fails_open_on_internal_error() -> None:
    """Buggy compute → returns None so caller falls back to legacy."""
    bad_settings = _settings()
    # Make classify_balance_tier blow up by removing the boundary dict.
    bad_settings.SIZING_TIER_BOUNDARIES = None  # type: ignore[assignment]
    result = await compute_dynamic_size(
        balance_usdt=1_000.0, confidence_pct=70.0, settings=bad_settings,
    )
    assert result is None


@pytest.mark.asyncio
async def test_dynamic_size_uses_calibrated_p_win_end_to_end() -> None:
    """A2 fix, end to end: real signal + flag on -> the calibrated
    p_win drives the Kelly compute, not the confidence proxy.
    p_win=0.9 (calibrated, NOT confidence_pct=50%/2=0.5) -> edge=0.8 ->
    quarter=0.2 -> whale-cap=0.10 -> $10k on a $100k balance."""
    with patch(
        "app.core.scoring.p_win_calibrator.predict_p_win",
        new=AsyncMock(return_value=0.9),
    ):
        result = await compute_dynamic_size(
            balance_usdt=100_000.0, confidence_pct=50.0, settings=_settings(),
            final_score=0.6, direction=Direction.LONG,
        )
    assert result is not None
    assert abs(result - 10_000.0) < 1e-9
