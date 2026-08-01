"""Pure-function tests for shadow_variant_rolling_report.

Covers cohort classification, decision rule, early-read flag, fee
assertion, and paired-group summarization. No DB dependency — the
SQL/report paths are exercised by the ops-debug probe against prod.
"""
from __future__ import annotations

import math

from scripts.shadow_variant_rolling_report import (
    DECISION_N,
    PairRow,
    _classify_cohort,
    _decision,
    _early_read,
    _fee_assertion_flag,
    _mean_se,
    _summarize_group,
)


# ---- cohort classification --------------------------------------------


def test_classify_atr_bound_when_atr_small() -> None:
    # entry=100, ATR=1.0 → 1.5*ATR = 1.5% ≤ 5% → atr_bound
    assert _classify_cohort(100.0, 1.0) == "atr_bound"


def test_classify_cap_bound_when_atr_large() -> None:
    # entry=100, ATR=4.0 → 1.5*ATR = 6.0% > 5% → cap_bound
    assert _classify_cohort(100.0, 4.0) == "cap_bound"


def test_classify_boundary_at_exactly_5pct_is_atr_bound() -> None:
    # 1.5*ATR = exactly 5% is NOT strictly greater — atr_bound.
    # Formula: engine.py caps at 5% inclusive; classifier mirrors that.
    assert _classify_cohort(100.0, 100.0 * 0.05 / 1.5) == "atr_bound"


def test_classify_degenerate_returns_atr_bound() -> None:
    """Zero/negative inputs default to atr_bound (primary cohort)."""
    assert _classify_cohort(0.0, 1.0) == "atr_bound"
    assert _classify_cohort(100.0, 0.0) == "atr_bound"


# ---- mean_se ---------------------------------------------------------


def test_mean_se_zero_when_empty() -> None:
    assert _mean_se([]) == (0.0, 0.0)


def test_mean_se_single_value_has_zero_se() -> None:
    m, se = _mean_se([1.5])
    assert m == 1.5
    assert se == 0.0


def test_mean_se_two_values() -> None:
    m, se = _mean_se([1.0, 3.0])
    assert m == 2.0
    # stdev([1,3]) = sqrt(2); se = sqrt(2)/sqrt(2) = 1.0
    assert math.isclose(se, 1.0, rel_tol=1e-9)


# ---- decision rule ---------------------------------------------------


def test_decision_not_yet_n_below_100() -> None:
    assert _decision(99, 0.5, 0.01) == "NOT-YET-N"


def test_decision_pass_positive_when_ci_clear_of_zero() -> None:
    # n=100, mean=0.5, se=0.2 → lo = 0.5 - 1.96*0.2 = 0.108 > 0 → PASS+
    assert "PASS+" in _decision(100, 0.5, 0.2)


def test_decision_pass_negative_when_ci_clear_of_zero_on_wrong_side() -> None:
    # n=100, mean=-0.5, se=0.2 → hi = -0.108 < 0 → PASS-
    assert "PASS-" in _decision(100, -0.5, 0.2)


def test_decision_fail_when_ci_includes_zero() -> None:
    # n=100, mean=0.2, se=0.5 → lo=-0.78, hi=1.18 → CI includes 0 → FAIL
    assert "FAIL" in _decision(100, 0.2, 0.5)


# ---- early directional read ------------------------------------------


def test_early_read_silent_below_threshold() -> None:
    # n=29 < EARLY_READ_N → silent
    assert _early_read(29, 0.5, 0.1) is None


def test_early_read_silent_when_ci_includes_zero() -> None:
    # n=30, mean=0.1, se=0.2 → lo=-0.1, hi=0.3 → silent (spans 0)
    assert _early_read(30, 0.1, 0.2) is None


def test_early_read_flags_strongly_positive() -> None:
    # n=30, mean=0.5, se=0.2 → lo = 0.5 - 1*0.2 = 0.3 > 0 → EARLY+
    got = _early_read(30, 0.5, 0.2)
    assert got is not None
    assert got.startswith("EARLY+")


def test_early_read_flags_strongly_negative() -> None:
    got = _early_read(30, -0.5, 0.2)
    assert got is not None
    assert got.startswith("EARLY-")


def test_early_read_stops_firing_once_full_decision_available() -> None:
    """At n>=DECISION_N the decision rule takes over — early read is
    suppressed to avoid double-reporting."""
    assert _early_read(DECISION_N, 0.5, 0.2) is None
    assert _early_read(DECISION_N + 100, 0.5, 0.2) is None


# ---- fee assertion ---------------------------------------------------


def test_fee_assertion_passes_when_gross_equals_after_fee() -> None:
    """Paired Δ_after_fee ≡ Δ_gross by construction (both lanes take
    same taker fees on same entry). Assertion holds → no bug."""
    gross = [0.5, -0.3, 1.2, -1.5]
    # after_fee constructed identically (deduct 0.10% from both sides
    # of each pair → cancels).
    after_fee = list(gross)
    assert _fee_assertion_flag(gross, after_fee) is None


def test_fee_assertion_flags_divergence_as_bug() -> None:
    """If any pair's Δ_gross ≠ Δ_after_fee beyond FEE_TOLERANCE, the
    probe must flag it (indicates a query bug or asymmetric fee model)."""
    gross = [0.5, -0.3]
    after_fee = [0.5, -0.4]  # bogus 0.1% divergence on the second pair
    flag = _fee_assertion_flag(gross, after_fee)
    assert flag is not None
    assert flag.startswith("[BUG]")


def test_fee_assertion_tolerates_floating_point_noise() -> None:
    """Sub-tolerance divergence (1e-10) must NOT flag."""
    gross = [0.5, -0.3]
    after_fee = [0.5 + 1e-10, -0.3 - 1e-10]
    assert _fee_assertion_flag(gross, after_fee) is None


# ---- summarize_group -------------------------------------------------


def _p(*, variant: str, cohort: str, tf: str,
       base: float, var: float, armed: bool) -> PairRow:
    trig = 0.40 if "0.40R" in variant else 0.50
    return PairRow(
        variant_name=variant, trigger_r=trig, timeframe=tf, cohort=cohort,
        base_pnl_pct=base, variant_pnl_pct=var, armed=armed,
    )


def test_summarize_empty_group() -> None:
    n, na, share, m, se = _summarize_group([])
    assert (n, na, share, m, se) == (0, 0, 0.0, 0.0, 0.0)


def test_summarize_paired_deltas() -> None:
    pairs = [
        _p(variant="breakeven_0.40R", cohort="atr_bound", tf="1h",
           base=-2.0, var=0.0, armed=True),   # Δ = +2.0
        _p(variant="breakeven_0.40R", cohort="atr_bound", tf="1h",
           base=+3.0, var=+3.0, armed=False), # Δ = 0.0
        _p(variant="breakeven_0.40R", cohort="atr_bound", tf="1h",
           base=-2.0, var=0.0, armed=True),   # Δ = +2.0
    ]
    n, na, share, m, se = _summarize_group(pairs)
    assert n == 3
    assert na == 2
    assert math.isclose(share, 200.0 / 3.0, rel_tol=1e-6)
    # Δs = [+2, 0, +2] → mean=4/3, stdev=sqrt(4/3), se=sqrt(4/3)/sqrt(3)
    assert math.isclose(m, 4.0 / 3.0, rel_tol=1e-9)
