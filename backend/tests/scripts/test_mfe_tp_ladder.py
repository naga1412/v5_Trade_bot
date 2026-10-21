"""Pure-function tests for the corrected TP_R ladder in mfe_mae_curve.

Covers the per-rung resolver (win / loss / same-bar tiebreak / timeout),
the paired noise floor's use of a FIXED endpoint pair, and the cell
aggregation. No DB and no network — the resolver takes bars directly.

Each test was mutation-checked: the mutation is named in the docstring
and was confirmed to turn the test red before being reverted.
"""
from __future__ import annotations

import math

from scripts.mfe_mae_curve import (
    LADDER_FLOOR_PAIR,
    TP_R_LADDER,
    Bar,
    TradeMetrics,
    _ladder_cell,
    _ladder_noise_floor,
    _resolve_at_tp_r,
)


def _tm(bars: list[Bar], *, entry: float = 100.0, sl: float = 90.0,
        actual_pnl_pct: float = 0.0) -> TradeMetrics:
    """A TradeMetrics with R = entry - sl = 10.0, i.e. R = 10% of entry."""
    r_unit = entry - sl
    return TradeMetrics(
        trade_id=1, scope="top20", atr_class="atr_bound", symbol="X",
        timeframe="1h", exit_reason="TAKE_PROFIT",
        actual_pnl_pct=actual_pnl_pct, entry_price=entry, stop_loss=sl,
        take_profit=entry + 2 * r_unit, bars=bars, r_unit=r_unit,
        r_pct=r_unit / entry,
    )


def _bar(high: float, low: float, ts: int = 0) -> Bar:
    return Bar(ts_ms=ts, high=high, low=low)


def test_win_at_rung_returns_exactly_that_rung() -> None:
    """TP at entry + k*R hit cleanly => outcome is exactly +k R.

    MUTATION: return 1.0 instead of tp_r -> red on every k except 1.0.
    """
    # high=115 clears 1.0R (110) and 1.25R (112.5) but not 2.0R (120).
    m = _tm([_bar(115.0, 99.0)])
    assert _resolve_at_tp_r(m, 0.5) == 0.5
    assert _resolve_at_tp_r(m, 1.0) == 1.0
    assert _resolve_at_tp_r(m, 1.25) == 1.25


def test_stop_loss_is_exactly_minus_one_r() -> None:
    """SL sits at entry - R by construction, so a loss is exactly -1R.

    MUTATION: return -tp_r -> red for every k != 1.0.
    """
    m = _tm([_bar(101.0, 89.0)])
    for k in TP_R_LADDER:
        assert _resolve_at_tp_r(m, k) == -1.0


def test_higher_rung_can_turn_a_win_into_a_loss() -> None:
    """The whole point of the ladder: raising TP makes the win harder.

    A bar that reaches 1.0R then later breaks the stop is a win at 0.5R
    and 1.0R, and a loss at 1.5R and 2.0R.

    MUTATION: `return 1.0` in place of `return tp_r` -> red. Verified.

    NOT a valid mutation for this test: dropping the early `break`. The
    loop records only the FIRST hit of each level (the `is None` guards),
    and the resolution compares bar indices, so the outcome is
    independent of the break -- it is a performance optimisation, not a
    correctness guard. Confirmed by mutation: removing it left every test
    green. Recorded so the break is not mistaken for load-bearing.
    """
    bars = [_bar(111.0, 99.0), _bar(100.0, 89.0)]
    m = _tm(bars)
    assert _resolve_at_tp_r(m, 0.5) == 0.5
    assert _resolve_at_tp_r(m, 1.0) == 1.0
    assert _resolve_at_tp_r(m, 1.5) == -1.0
    assert _resolve_at_tp_r(m, 2.0) == -1.0


def test_same_bar_sl_and_tp_resolves_to_sl() -> None:
    """Production check_exit breaks a same-bar tie in favour of the stop.

    HONEST NOTE ON WHAT THIS TEST CAN AND CANNOT CATCH. The tie-break is
    enforced TWICE: by the `not (hit_sl and sl_bar == i)` guard in the bar
    loop, and again by the resolution comparison, since on a tie
    `tp_bar < sl_bar` is `0 < 0` = False and control falls through to the
    SL branch. Removing EITHER enforcement alone leaves the outcome
    unchanged, so this is an OUTCOME test, not a guard test, and no
    single-site mutation turns it red. Verified by mutation rather than
    assumed: removing the loop guard left all tests green.

    It is kept because the outcome is the thing that must hold, and
    labelled so nobody reads it as protecting the guard.
    """
    m = _tm([_bar(125.0, 85.0)])  # clears 2.0R AND breaks the stop, same bar
    for k in TP_R_LADDER:
        assert _resolve_at_tp_r(m, k) == -1.0


def test_timeout_falls_back_to_realised_pnl_in_r_units() -> None:
    """Neither level touched => use the trade's own pnl, expressed in R.

    R is 10% of entry here, so a realised +3% is +0.3R.

    MUTATION: return 0.0 for the timeout branch -> red.
    """
    m = _tm([_bar(104.0, 97.0)], actual_pnl_pct=3.0)
    assert _resolve_at_tp_r(m, 2.0) == 0.3


def test_zero_r_unit_is_inert_rather_than_dividing_by_zero() -> None:
    m = _tm([_bar(104.0, 97.0)], entry=100.0, sl=100.0)
    assert _resolve_at_tp_r(m, 1.0) == 0.0


def test_noise_floor_uses_the_fixed_pair_and_not_another_pair() -> None:
    """The floor must be a function of the PRE-CHOSEN endpoint pair.

    The first fixture attempt did NOT discriminate: it made the
    0.5R-vs-2.0R difference constant, but the 1.0R-vs-1.5R difference was
    constant too, so both pairs yielded a 0.0 floor and swapping them
    left the test green. Verified by mutation, not assumed.

    This fixture makes the two pairs give genuinely different floors, so
    the assertion can only pass for the fixed pair:

      trade A wins at 0.5R/1.0R then stops out -> d(0.5,2.0) = -1.5,
                                                  d(1.0,1.5) = -2.0
      trade B wins at every rung              -> d(0.5,2.0) = +1.5,
                                                  d(1.0,1.5) = +0.5

    MUTATION: `lo, hi = (1.0, 1.5)` -> floor becomes 2.5 and this is red.
    """
    assert LADDER_FLOOR_PAIR == (0.5, 2.0)
    a = _tm([_bar(110.0, 99.0), _bar(100.0, 89.0)])  # 0.5R,1.0R win; 1.5R+ loss
    b = _tm([_bar(125.0, 99.0)])                      # wins at every rung
    floor, n = _ladder_noise_floor([a, b])
    assert n == 2
    # Fixed pair: diffs [-1.5, +1.5] -> sd 2.1213, SE 1.5, floor 3.0
    assert math.isclose(floor, 3.0, rel_tol=1e-9)
    # The (1.0, 1.5) pair would give 2.5 — different, so the test discriminates.
    assert not math.isclose(floor, 2.5, rel_tol=1e-9)


def test_noise_floor_is_positive_when_the_pair_difference_varies() -> None:
    a = _tm([_bar(106.0, 99.0), _bar(100.0, 89.0)])   # 0.5R win, 2.0R loss
    b = _tm([_bar(125.0, 99.0)])                       # wins at both rungs
    floor, n = _ladder_noise_floor([a, b, a, b])
    assert n == 4
    assert floor > 0.0


def test_ladder_cell_reports_n_wr_and_fee_adjusted_expectancy() -> None:
    """A cell of three winners at 1.0R: WR 100%, exp_R 1.0, fee < gross.

    MUTATION: drop the fee subtraction -> exp_r_fee == exp_r and the
    strict inequality goes red.
    """
    subset = [_tm([_bar(111.0, 99.0)]) for _ in range(3)]
    c = _ladder_cell(subset, 1.0)
    assert c["n"] == 3
    assert c["wr"] == 100.0
    assert math.isclose(c["exp_r"], 1.0)
    assert c["exp_r_fee"] < c["exp_r"]
    assert math.isclose(c["se"], 0.0, abs_tol=1e-12)


def test_empty_cell_is_reported_rather_than_crashing() -> None:
    assert _ladder_cell([], 1.0) == {"n": 0}
