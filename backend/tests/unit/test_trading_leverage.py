"""SP-8 Phase C — leverage math.

Direct table-driven tests against spec §6.2 worked examples. Every
row in the spec table maps to one test case here; if the spec table
ever changes, this file is the regression bar.
"""
from __future__ import annotations

import pytest

from app.trading.leverage import (
    liquidation_distance_pct,
    loss_at_stop_loss_usdt,
    recommended_leverage,
)


# (sl_distance_pct, expected_recommended_leverage) — directly from spec §6.2.
@pytest.mark.parametrize("sl_pct,expected_lev", [
    (0.01, 10),   # 1% SL → math max 80, capped at 10
    (0.02, 10),   # 2% SL → math max 40, capped at 10
    (0.03, 10),   # 3% SL → math max 26, capped at 10
    (0.05, 10),   # 5% SL → math max 16, capped at 10
    (0.08, 10),   # 8% SL → math max 10, exactly the cap
    (0.10, 8),    # 10% SL → math max 8
    (0.15, 5),    # 15% SL → math max 5
])
def test_recommended_leverage_matches_spec_section_6_2(
    sl_pct: float, expected_lev: int,
) -> None:
    """Every row in the spec table must round-trip exactly."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=sl_pct) == expected_lev


def test_zero_or_negative_sl_returns_one_x() -> None:
    """Always-safe fallback when SL distance is degenerate."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0) == 1
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=-0.05) == 1


def test_very_wide_sl_returns_one_x() -> None:
    """SL ≥ 80% → math < 1× → return 1×."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0.80) == 1
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0.99) == 1


def test_hard_cap_caps_above_math_safe() -> None:
    """When user passes hard_cap=20 and math allows 40, return 20."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0.02, hard_cap=20) == 20


def test_hard_cap_one_clamps_everything_to_one() -> None:
    """User explicitly setting hard_cap=1 forces 1× regardless of math."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0.001, hard_cap=1) == 1


def test_invalid_hard_cap_returns_safe_one() -> None:
    """hard_cap < 1 is caller error; we return 1× rather than raise."""
    assert recommended_leverage(margin_usdt=30, sl_distance_pct=0.02, hard_cap=0) == 1


def test_liquidation_distance_inverse_of_leverage() -> None:
    assert liquidation_distance_pct(10) == pytest.approx(0.10)
    assert liquidation_distance_pct(5) == pytest.approx(0.20)
    assert liquidation_distance_pct(1) == pytest.approx(1.0)


def test_liquidation_distance_zero_for_invalid_leverage() -> None:
    assert liquidation_distance_pct(0) == 0.0
    assert liquidation_distance_pct(-1) == 0.0


def test_loss_at_sl_matches_spec_section_6_2_loss_column() -> None:
    """Spec §6.2 final column: $30 margin × leverage × SL_pct."""
    # 1% SL @ 10×: $30 × 10 × 0.01 = $3.00
    assert loss_at_stop_loss_usdt(30, 10, 0.01) == pytest.approx(3.00)
    # 2% SL @ 10×: $30 × 10 × 0.02 = $6.00
    assert loss_at_stop_loss_usdt(30, 10, 0.02) == pytest.approx(6.00)
    # 8% SL @ 10×: $30 × 10 × 0.08 = $24.00
    assert loss_at_stop_loss_usdt(30, 10, 0.08) == pytest.approx(24.00)
    # 15% SL @ 5×: $30 × 5 × 0.15 = $22.50
    assert loss_at_stop_loss_usdt(30, 5, 0.15) == pytest.approx(22.50)


def test_loss_at_sl_zero_for_invalid_inputs() -> None:
    assert loss_at_stop_loss_usdt(0, 10, 0.02) == 0.0
    assert loss_at_stop_loss_usdt(30, 0, 0.02) == 0.0
    assert loss_at_stop_loss_usdt(30, 10, 0) == 0.0
    assert loss_at_stop_loss_usdt(-1, 10, 0.02) == 0.0
