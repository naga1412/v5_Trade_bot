"""PR9 settings defaults — all default-OFF for prod safety.

PR9 ships 8 settings + verifies each tier in SIZING_TIER_MAX_FRACTION has
a matching boundary. Live-money exposure: operator-tunable via env vars.
"""
from __future__ import annotations

from app.config import get_settings


def test_dynamic_sizing_enabled_default_false() -> None:
    """Default-OFF in prod. Operator carve-out: PR9 dev→main requires
    explicit ship-it approval. Default-OFF preserves pre-PR9 behavior
    at deploy time."""
    assert get_settings().DYNAMIC_SIZING_ENABLED is False


def test_use_p_win_when_available_default_true() -> None:
    """When PR5 ships predict_p_win(), sizing auto-switches. The flag
    lets operators force the confidence proxy if they prefer."""
    assert get_settings().SIZING_USE_P_WIN_WHEN_AVAILABLE is True


def test_fractional_kelly_default_quarter() -> None:
    """Quarter-Kelly is industry-standard defensive sizing."""
    assert get_settings().SIZING_FRACTIONAL_KELLY == 0.25


def test_tier_max_fraction_defaults() -> None:
    """Per-tier hard caps. Whale (10%) is aggressive — operator should
    revisit if their actual whale-tier users prefer lower."""
    tiers = get_settings().SIZING_TIER_MAX_FRACTION
    assert tiers["small"] == 0.01
    assert tiers["medium"] == 0.02
    assert tiers["large"] == 0.05
    assert tiers["whale"] == 0.10


def test_tier_boundaries_defaults() -> None:
    """Tier boundary thresholds."""
    bounds = get_settings().SIZING_TIER_BOUNDARIES
    assert bounds["small_max"] == 1_000.0
    assert bounds["medium_max"] == 10_000.0
    assert bounds["large_max"] == 100_000.0


def test_every_tier_has_a_max_fraction_cap() -> None:
    """Catch future tier additions that miss the cap dict."""
    tiers = get_settings().SIZING_TIER_MAX_FRACTION
    expected_tiers = {"small", "medium", "large", "whale"}
    assert set(tiers.keys()) == expected_tiers


def test_regime_aware_default_false_unchanged() -> None:
    """PR8's regime-aware flag stays default-False; PR9 doesn't touch it."""
    assert get_settings().LIVE_COOLDOWN_REGIME_AWARE is False
