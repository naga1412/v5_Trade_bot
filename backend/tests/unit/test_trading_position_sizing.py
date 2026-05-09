"""SP-8 Phase C — position sizing.

Verifies fixed mode (single value + range) and the percent-mode ramp
defined in spec §5.2.
"""
from __future__ import annotations

import random

import pytest

from app.trading.position_sizing import (
    FixedSizingConfig,
    compute_position_margin,
    percent_sizing_amount,
)


# ---- Fixed mode ----------------------------------------------------------


def test_fixed_single_value_returns_exact_amount() -> None:
    cfg = FixedSizingConfig(amount_usdt=30.0)
    assert cfg.resolve() == 30.0


def test_fixed_range_uniform_within_bounds() -> None:
    """Range $20-$50 → result must be in [20, 50] for any seed."""
    cfg = FixedSizingConfig(range_low_usdt=20.0, range_high_usdt=50.0)
    rng = random.Random(42)
    for _ in range(100):
        v = cfg.resolve(rng=rng)
        assert 20.0 <= v <= 50.0


def test_fixed_range_swapped_bounds_handled() -> None:
    """If user accidentally passes high<low, we swap them."""
    cfg = FixedSizingConfig(range_low_usdt=50.0, range_high_usdt=20.0)
    rng = random.Random(42)
    v = cfg.resolve(rng=rng)
    assert 20.0 <= v <= 50.0


def test_fixed_amount_wins_over_range_when_both_set() -> None:
    cfg = FixedSizingConfig(amount_usdt=30.0, range_low_usdt=10, range_high_usdt=20)
    assert cfg.resolve() == 30.0


def test_fixed_neither_set_raises() -> None:
    cfg = FixedSizingConfig()
    with pytest.raises(ValueError, match="amount_usdt"):
        cfg.resolve()


# ---- Percent mode --------------------------------------------------------


@pytest.mark.parametrize("trades,expected_pct", [
    (0,    0.001),     # tier 1 lower edge
    (29,   0.001),     # tier 1 upper edge (exclusive)
    (30,   0.0025),    # tier 2 lower edge
    (99,   0.0025),    # tier 2 upper edge
    (100,  0.005),     # tier 3 lower edge
    (499,  0.005),     # tier 3 upper edge
    (500,  0.01),      # tier 4 (Half-Kelly cap)
    (10_000, 0.01),    # well past tier 4 — still caps at 1%
])
def test_percent_ramp_matches_spec_section_5_2(
    trades: int, expected_pct: float,
) -> None:
    portfolio = 1000.0
    assert percent_sizing_amount(portfolio, trades) == pytest.approx(
        portfolio * expected_pct
    )


def test_percent_zero_portfolio_returns_zero() -> None:
    assert percent_sizing_amount(0, 50) == 0.0


def test_percent_negative_trades_returns_zero() -> None:
    """Defensive: negative successful_trades is caller bug → return 0."""
    assert percent_sizing_amount(1000, -1) == 0.0


# ---- Top-level dispatch --------------------------------------------------


def test_compute_position_margin_fixed_dispatches_to_fixed() -> None:
    cfg = FixedSizingConfig(amount_usdt=42.0)
    assert compute_position_margin(mode="fixed", fixed_config=cfg) == 42.0


def test_compute_position_margin_percent_dispatches_to_percent() -> None:
    assert compute_position_margin(
        mode="percent",
        portfolio_value_usdt=10_000,
        successful_trades=50,
    ) == pytest.approx(25.0)  # 0.25% of $10k


def test_compute_position_margin_fixed_without_config_raises() -> None:
    with pytest.raises(ValueError, match="fixed_config"):
        compute_position_margin(mode="fixed")


def test_compute_position_margin_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unsupported sizing mode"):
        compute_position_margin(mode="weird")  # type: ignore[arg-type]
