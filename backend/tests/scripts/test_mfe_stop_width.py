"""Pure-function tests for the stop-width conversion test.

Covers the widened-stop resolver (conversion, deeper loss, timeout,
same-bar tiebreak), the baseline-equivalence property that makes width
1.0 trustworthy, and the expectancy aggregation. No DB, no network.

Every mutation named below was run and confirmed to turn the test red.
Mutations found NOT to bite are named as such — a docstring that claims
a mutation it has not been checked against is the failure this repo has
recorded nine times.
"""
from __future__ import annotations

import math

from scripts.mfe_mae_curve import (
    STOP_WIDTHS,
    Bar,
    TradeMetrics,
    _resolve_at_stop_mult,
    _stop_expectancy,
)


def _tm(bars: list[Bar], *, entry: float = 100.0, sl: float = 90.0,
        tp: float = 120.0, actual_pnl_pct: float = 0.0) -> TradeMetrics:
    """R = entry - sl = 10.0, i.e. R = 10% of entry. TP defaults to +2R."""
    return TradeMetrics(
        trade_id=1, scope="top20", atr_class="atr_bound", symbol="X",
        timeframe="1h", symbol_source="established_top20",
        exit_reason="STOP_LOSS", actual_pnl_pct=actual_pnl_pct,
        entry_price=entry, stop_loss=sl, take_profit=tp, bars=bars,
        r_unit=entry - sl, r_pct=(entry - sl) / entry,
    )


def _bar(high: float, low: float, ts: int = 0) -> Bar:
    return Bar(ts_ms=ts, high=high, low=low)


def test_a_stop_out_converts_when_the_stop_is_widened() -> None:
    """THE TEST THE WHOLE PROBE EXISTS FOR.

    Bar 0 dips to 88 (through the 1.0x stop at 90, but not through the
    1.5x stop at 85) and bar 1 reaches the actual TP at 120. So this
    trade is a loss at 1.0x and a conversion at 1.5x and 2.0x.

    MUTATION: `sl_price = m.entry_price - m.r_unit` (ignore width) -> the
    1.5x/2.0x assertions go red. Verified.
    """
    m = _tm([_bar(101.0, 88.0), _bar(121.0, 100.0)])
    assert _resolve_at_stop_mult(m, 1.0)[0] == "loss"
    assert _resolve_at_stop_mult(m, 1.5)[0] == "win"
    assert _resolve_at_stop_mult(m, 2.0)[0] == "win"


def test_a_win_pays_the_actual_tp_distance_not_the_width() -> None:
    """A conversion pays the trade's real TP distance in R (here +2R),
    NOT the stop width that let it survive.

    MUTATION: `return "win", width` -> red at 1.5x (would pay 1.5).
    Verified.
    """
    m = _tm([_bar(101.0, 88.0), _bar(121.0, 100.0)], tp=120.0)
    outcome, r = _resolve_at_stop_mult(m, 1.5)
    assert outcome == "win"
    assert math.isclose(r, 2.0)


def test_a_loss_at_width_w_costs_exactly_minus_w() -> None:
    """The cost side of the trade-off, by construction.

    MUTATION: `return "loss", -1.0` -> red at every width != 1.0.
    Verified.
    """
    m = _tm([_bar(101.0, 70.0)])  # blows through every width
    for w in STOP_WIDTHS:
        outcome, r = _resolve_at_stop_mult(m, w)
        assert outcome == "loss"
        assert math.isclose(r, -w)


def test_width_one_reproduces_the_baseline_resolution() -> None:
    """Width 1.0 must agree with the script's own baseline model, or the
    whole comparison is against a different thing than production.

    This is the property that makes the baseline column trustworthy:
    at width 1.0 the simulated stop price IS the actual stop_loss.
    """
    # loser
    a = _tm([_bar(101.0, 89.0)])
    assert _resolve_at_stop_mult(a, 1.0)[0] == "loss"
    # winner
    b = _tm([_bar(121.0, 99.0)])
    assert _resolve_at_stop_mult(b, 1.0)[0] == "win"
    # neither
    c = _tm([_bar(104.0, 97.0)], actual_pnl_pct=1.5)
    assert _resolve_at_stop_mult(c, 1.0)[0] == "timeout"


def test_same_bar_stop_and_target_resolves_to_the_stop() -> None:
    """Production tiebreak. Outcome test, not a guard test.

    NOT a valid mutation: removing the `not (hit_sl and sl_bar == i)`
    guard. On a tie `tp_bar < sl_bar` is 0<0=False, so control already
    falls through to the loss branch — the tiebreak is enforced twice and
    no single-site removal changes the outcome. Confirmed by mutation on
    the sibling ladder resolver, which shares this shape.
    """
    m = _tm([_bar(125.0, 70.0)])
    for w in STOP_WIDTHS:
        assert _resolve_at_stop_mult(m, w)[0] == "loss"


def test_timeout_falls_back_to_realised_pnl_in_r_units() -> None:
    """MUTATION: `return "timeout", 0.0` -> red. Verified."""
    m = _tm([_bar(104.0, 97.0)], actual_pnl_pct=2.5)
    outcome, r = _resolve_at_stop_mult(m, 2.0)
    assert outcome == "timeout"
    assert math.isclose(r, 0.25)  # 2.5% realised / 10% per R


def test_widening_can_only_help_a_trade_that_already_won() -> None:
    """Monotonicity sanity: a baseline winner cannot be made a loser by a
    WIDER stop. If this ever fails the resolver has a sign error.
    """
    winner = _tm([_bar(121.0, 99.0)])
    for w in STOP_WIDTHS:
        assert _resolve_at_stop_mult(winner, w)[0] == "win"


def test_expectancy_reports_n_r_and_percent() -> None:
    """exp_pct must be exp_R scaled by the mean R-in-percent, so the
    result can be read in pnl_pct units.

    MUTATION: drop the `* mean_r_pct` scaling -> exp_pct == exp_r and the
    0.2 assertion goes red. Verified.
    """
    # Three identical +2R winners, R = 10% of entry => +20% per trade.
    subset = [_tm([_bar(121.0, 99.0)]) for _ in range(3)]
    st = _stop_expectancy(subset, 1.0)
    assert st["n"] == 3
    assert math.isclose(st["exp_r"], 2.0)
    assert math.isclose(st["mean_r_pct"], 10.0)
    assert math.isclose(st["exp_pct"], 20.0)
    assert st["exp_r_fee"] < st["exp_r"]


def test_empty_subset_is_reported_rather_than_crashing() -> None:
    assert _stop_expectancy([], 1.5) == {"n": 0}


def test_zero_r_unit_is_inert() -> None:
    m = _tm([_bar(104.0, 97.0)], entry=100.0, sl=100.0)
    assert _resolve_at_stop_mult(m, 1.5) == ("unknown", 0.0)
