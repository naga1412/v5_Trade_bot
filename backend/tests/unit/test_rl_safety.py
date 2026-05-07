"""Tests for app.rl.safety (SP-4 Phase C1)."""
from __future__ import annotations

from collections import deque

import pytest

from app.rl.replay_buffer import (
    ACTION_FLAT,
    ACTION_LONG_FULL,
    ACTION_LONG_HALF,
    ACTION_SHORT_FULL,
    ACTION_SHORT_HALF,
)
from app.rl.safety import (
    DRAWDOWN_BREAKER_THRESHOLD,
    DRAWDOWN_BREAKER_WINDOW_DAYS,
    MAX_POSITION_FRACTION,
    SMOOTHING_WINDOW,
    TURNOVER_CAP_PENALTY,
    TURNOVER_CAP_PER_DAY,
    assert_no_leverage,
    evaluate_drawdown,
    smooth_action,
    turnover_cap_penalty,
    update_history_inplace,
)


def test_constants_match_spec_section_7() -> None:
    assert TURNOVER_CAP_PER_DAY == 12
    assert TURNOVER_CAP_PENALTY == -1.0
    assert DRAWDOWN_BREAKER_THRESHOLD == 0.15
    assert DRAWDOWN_BREAKER_WINDOW_DAYS == 7
    assert SMOOTHING_WINDOW == 3
    assert MAX_POSITION_FRACTION == 1.0


# ---- smooth_action ------------------------------------------------------


def test_smooth_action_empty_history_returns_raw() -> None:
    assert smooth_action([], ACTION_LONG_FULL) == ACTION_LONG_FULL


def test_smooth_action_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown raw_action"):
        smooth_action([ACTION_FLAT], "BUY_THE_MOON")


def test_smooth_action_majority_wins() -> None:
    """3-window: FLAT, FLAT, LONG_FULL → smoothed = FLAT."""
    history = [ACTION_FLAT, ACTION_FLAT]
    assert smooth_action(history, ACTION_LONG_FULL) == ACTION_FLAT


def test_smooth_action_unanimous_passes_through() -> None:
    history = [ACTION_LONG_FULL, ACTION_LONG_FULL]
    assert smooth_action(history, ACTION_LONG_FULL) == ACTION_LONG_FULL


def test_smooth_action_tie_prefers_flat() -> None:
    """LONG_FULL, SHORT_FULL, FLAT — three different actions, FLAT wins tie."""
    history = [ACTION_LONG_FULL, ACTION_SHORT_FULL]
    assert smooth_action(history, ACTION_FLAT) == ACTION_FLAT


def test_smooth_action_no_flat_in_tie_falls_back_to_raw() -> None:
    """LONG_FULL, SHORT_FULL — tied; no FLAT; returns raw_action."""
    history = [ACTION_LONG_FULL]
    out = smooth_action(history, ACTION_SHORT_FULL)
    # Counter is {LONG_FULL: 1, SHORT_FULL: 1}; FLAT not present;
    # function returns raw_action.
    assert out == ACTION_SHORT_FULL


def test_smooth_action_uses_only_last_window() -> None:
    """Old actions further than SMOOTHING_WINDOW shouldn't influence output."""
    long_run = [ACTION_LONG_FULL] * 50
    long_run += [ACTION_FLAT, ACTION_FLAT]  # last 2 are FLAT
    out = smooth_action(long_run, ACTION_FLAT)
    # Window is last 2 from history + new raw (FLAT) = 3 FLATs.
    assert out == ACTION_FLAT


def test_update_history_inplace_uses_deque_maxlen() -> None:
    history: deque[str] = deque(maxlen=SMOOTHING_WINDOW)
    for action in [ACTION_LONG_FULL, ACTION_LONG_HALF, ACTION_FLAT,
                   ACTION_SHORT_HALF]:
        update_history_inplace(history, action)
    assert len(history) == SMOOTHING_WINDOW
    assert list(history)[-1] == ACTION_SHORT_HALF
    # The first appended (LONG_FULL) should have been kicked out.
    assert ACTION_LONG_FULL not in history


# ---- drawdown breaker --------------------------------------------------


def test_evaluate_drawdown_no_history_no_trigger() -> None:
    state = evaluate_drawdown([])
    assert state.triggered is False
    assert state.drawdown_pct == 0.0


def test_evaluate_drawdown_single_point_no_trigger() -> None:
    state = evaluate_drawdown([100.0])
    assert state.triggered is False


def test_evaluate_drawdown_flat_equity_no_drawdown() -> None:
    state = evaluate_drawdown([100.0, 100.0, 100.0])
    assert state.drawdown_pct == 0.0
    assert state.triggered is False


def test_evaluate_drawdown_steady_decline_triggers_above_threshold() -> None:
    """Equity 100 → 80 = 20% DD; threshold 15% → triggers."""
    state = evaluate_drawdown([100.0, 95.0, 90.0, 85.0, 80.0])
    assert state.peak == 100.0
    assert state.trough == 80.0
    assert state.drawdown_pct == pytest.approx(0.20)
    assert state.triggered is True


def test_evaluate_drawdown_recovery_doesnt_reset_max_drawdown() -> None:
    """Equity 100 → 80 → 95 — max DD is still 20% even after partial recovery."""
    state = evaluate_drawdown([100.0, 80.0, 95.0])
    assert state.drawdown_pct == pytest.approx(0.20)


def test_evaluate_drawdown_under_threshold_no_trigger() -> None:
    state = evaluate_drawdown([100.0, 90.0])  # 10% DD < 15% threshold
    assert state.drawdown_pct == pytest.approx(0.10)
    assert state.triggered is False


def test_evaluate_drawdown_custom_threshold() -> None:
    state = evaluate_drawdown([100.0, 95.0], threshold=0.04)
    assert state.triggered is True


# ---- turnover cap ------------------------------------------------------


def test_turnover_cap_under_cap_returns_zero() -> None:
    assert turnover_cap_penalty(0) == 0.0
    assert turnover_cap_penalty(11) == 0.0
    assert turnover_cap_penalty(TURNOVER_CAP_PER_DAY) == 0.0


def test_turnover_cap_over_cap_returns_penalty() -> None:
    assert turnover_cap_penalty(13) == TURNOVER_CAP_PENALTY
    assert turnover_cap_penalty(100) == TURNOVER_CAP_PENALTY


def test_turnover_cap_custom_cap() -> None:
    assert turnover_cap_penalty(5, cap=4) == TURNOVER_CAP_PENALTY
    assert turnover_cap_penalty(4, cap=4) == 0.0


def test_turnover_cap_custom_penalty() -> None:
    assert turnover_cap_penalty(20, penalty=-2.5) == -2.5


# ---- no-leverage assertion ---------------------------------------------


def test_assert_no_leverage_passes_at_or_below_max() -> None:
    assert_no_leverage(0.0)
    assert_no_leverage(0.5)
    assert_no_leverage(1.0)


def test_assert_no_leverage_raises_above_max() -> None:
    with pytest.raises(ValueError, match="exceeds no-leverage cap"):
        assert_no_leverage(1.1)
    with pytest.raises(ValueError, match="exceeds no-leverage cap"):
        assert_no_leverage(2.0)


def test_assert_no_leverage_rejects_negative() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        assert_no_leverage(-0.1)
