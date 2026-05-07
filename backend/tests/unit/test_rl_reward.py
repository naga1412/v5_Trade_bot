"""Tests for app.rl.reward.compute_reward (SP-4 Phase A3)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.rl.reward import (
    MIN_TRADES_FOR_SIGMA,
    PRIOR_SIGMA,
    RECENT_TRADES_WINDOW,
    REWARD_CLIP,
    RISK_AVERSION_WEIGHT,
    compute_reward,
)


@dataclass(frozen=True)
class FakeTrade:
    pnl_quote: float
    initial_risk_quote: float


def test_constants_match_spec_section_4() -> None:
    assert RECENT_TRADES_WINDOW == 20
    assert RISK_AVERSION_WEIGHT == 0.5
    assert PRIOR_SIGMA == 1.0
    assert REWARD_CLIP == 3.0
    assert MIN_TRADES_FOR_SIGMA == 5


def test_no_history_uses_prior_sigma() -> None:
    """With <MIN_TRADES_FOR_SIGMA recent trades, σ defaults to 1.0."""
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)  # +2R
    r = compute_reward(trade, recent=[])
    assert r == pytest.approx(2.0 - 0.5 * 1.0)


def test_partial_history_uses_prior_sigma() -> None:
    """4 recent trades < MIN_TRADES_FOR_SIGMA → still prior."""
    recent = [FakeTrade(pnl_quote=100.0, initial_risk_quote=100.0)] * 4
    trade = FakeTrade(pnl_quote=300.0, initial_risk_quote=100.0)  # +3R
    r = compute_reward(trade, recent=recent)
    assert r == pytest.approx(3.0 - 0.5 * 1.0)


def test_negative_R_clipped_to_minus_three() -> None:
    trade = FakeTrade(pnl_quote=-1000.0, initial_risk_quote=100.0)  # -10R
    r = compute_reward(trade, recent=[])
    assert r == -REWARD_CLIP


def test_positive_R_clipped_to_plus_three() -> None:
    trade = FakeTrade(pnl_quote=1000.0, initial_risk_quote=100.0)  # +10R
    r = compute_reward(trade, recent=[])
    assert r == REWARD_CLIP


def test_sigma_computed_from_recent_R_multiples() -> None:
    """6 recent trades (3 wins +1R, 3 losses -1R) → σ = 1.0."""
    recent = (
        [FakeTrade(pnl_quote=100, initial_risk_quote=100) for _ in range(3)]
        + [FakeTrade(pnl_quote=-100, initial_risk_quote=100) for _ in range(3)]
    )
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)  # +2R
    r = compute_reward(trade, recent=recent)
    # population std of [1,1,1,-1,-1,-1] = 1.0
    assert r == pytest.approx(2.0 - 0.5 * 1.0, abs=1e-6)


def test_sigma_uses_only_last_recent_window_trades() -> None:
    """Earlier wild trades are dropped from σ calc; only the trailing window matters."""
    # 50 wild trades (R=100), then 20 stable trades (R=1).
    recent = (
        [FakeTrade(pnl_quote=10000, initial_risk_quote=100) for _ in range(50)]
        + [FakeTrade(pnl_quote=100, initial_risk_quote=100) for _ in range(RECENT_TRADES_WINDOW)]
    )
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)
    r = compute_reward(trade, recent=recent)
    # σ over the trailing 20 (all R=1) = 0; reward = 2 - 0.5*0 = 2.
    assert r == pytest.approx(2.0)


def test_zero_initial_risk_raises() -> None:
    trade = FakeTrade(pnl_quote=100.0, initial_risk_quote=0.0)
    with pytest.raises(ValueError, match="initial_risk_quote must be > 0"):
        compute_reward(trade, recent=[])


def test_negative_initial_risk_raises() -> None:
    trade = FakeTrade(pnl_quote=100.0, initial_risk_quote=-10.0)
    with pytest.raises(ValueError, match="initial_risk_quote must be > 0"):
        compute_reward(trade, recent=[])


def test_recent_with_zero_risk_skipped_not_crashing() -> None:
    """Defensive: a zero-risk row in recent is skipped, not raised."""
    recent = (
        [FakeTrade(pnl_quote=100, initial_risk_quote=100) for _ in range(MIN_TRADES_FOR_SIGMA)]
        + [FakeTrade(pnl_quote=10, initial_risk_quote=0)]  # bad row
    )
    trade = FakeTrade(pnl_quote=200.0, initial_risk_quote=100.0)
    # Should not raise. σ over the 5 valid trades (all R=1) = 0; reward = 2.
    r = compute_reward(trade, recent=recent)
    assert r == pytest.approx(2.0)


def test_reward_is_finite_for_typical_inputs() -> None:
    """Sanity: reward stays finite + non-NaN over a bunch of random fixtures."""
    import random
    rng = random.Random(123)
    recent: list[FakeTrade] = []
    for _ in range(200):
        pnl = rng.uniform(-300, 300)
        risk = rng.uniform(50, 500)
        trade = FakeTrade(pnl_quote=pnl, initial_risk_quote=risk)
        r = compute_reward(trade, recent=recent)
        assert -REWARD_CLIP <= r <= REWARD_CLIP
        recent.append(trade)
