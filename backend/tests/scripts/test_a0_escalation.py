"""Pure-function tests for the A0 full-range escalation.

The statistical content that is NEW here is the cluster-robust SE, so
that is where the tests concentrate. The headline property is an
identity, not an approximation: the CR1 sandwich for a saturated
two-group model reduces EXACTLY to the naive independent-rows SE when
every row is its own cluster. That identity is what makes the reported
design effect interpretable -- it is entirely within-symbol correlation
and not an artefact of switching estimators.

Every mutation named below was RUN and confirmed to turn the test red.
Nothing is asserted about a mutation that was not executed.
"""
from __future__ import annotations

import math

from scripts.a0_escalation import (
    ATR_PERIOD,
    BAR_SIGMA,
    BAR_SIGMA_CORRECTED,
    HORIZONS,
    NOMINATED_HORIZON,
    SL_ATR_MULT,
    SL_CAP_FRAC,
    Row,
    atr_at,
    cluster_robust_contrast,
    design_effect,
    excursions_at_horizons,
    naive_contrast,
    r_unit,
    thin_non_overlapping,
    true_ranges,
    verdict,
)
from scripts.mfe_mae_curve import Bar


def _bar(ts: int, hi: float, lo: float, close: float) -> Bar:
    return Bar(ts_ms=ts, high=hi, low=lo, close=close)


# --- the identity: CR == naive when clusters are singletons -------------

def test_cluster_robust_equals_naive_when_every_row_is_its_own_cluster() -> None:
    """THE CENTRAL PROPERTY, and it is an identity rather than a bound.

    With singleton clusters the sandwich collapses to
    SS0/N0^2 + SS1/N1^2, which is the naive variance using the N (not
    N-1) denominator. The CR1 finite-sample factor is divided back out
    here so the comparison is exact rather than approximate.

    MUTATION: drop the `2.0 *` from the cross term in the meat -> red.
    MUTATION: use +1/n0 instead of -1/n0 for `a` -> red.
    """
    lo = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    hi = [2.0, 4.0, 3.0, 7.0, 5.0, 9.0]
    rows = [(f"c{i}", v, False) for i, v in enumerate(lo)]
    rows += [(f"d{i}", v, True) for i, v in enumerate(hi)]

    cr = cluster_robust_contrast(rows)
    assert cr["ok"]

    n0, n1 = len(lo), len(hi)
    n, g = n0 + n1, n0 + n1
    cr1 = (g / (g - 1.0)) * ((n - 1.0) / (n - 2.0))
    var_uncorrected = cr["se"] ** 2 / cr1

    m0, m1 = sum(lo) / n0, sum(hi) / n1
    ss0 = sum((v - m0) ** 2 for v in lo)
    ss1 = sum((v - m1) ** 2 for v in hi)
    expected = ss0 / n0 ** 2 + ss1 / n1 ** 2

    assert math.isclose(var_uncorrected, expected, rel_tol=1e-9)


def test_cluster_robust_diff_is_hi_minus_lo() -> None:
    """Sign convention. MUTATION: return m0 - m1 -> red.

    Values must VARY within each group: with zero residual variance the
    estimator correctly refuses (see the degenerate-case test below), so
    a constant-valued fixture would test nothing.
    """
    rows = [("a", 1.0, False), ("b", 3.0, False),
            ("c", 4.0, True), ("d", 8.0, True)]
    assert math.isclose(cluster_robust_contrast(rows)["diff"], 4.0)


def test_cluster_robust_refuses_a_degenerate_zero_variance_fixture() -> None:
    """Found by writing a bad test, kept because it documents real
    behaviour: if the contrast is IDENTICAL in every cluster there is no
    between-cluster variation in the effect, the sandwich variance is
    exactly zero, and a sigma would be infinite. Refusing is correct;
    reporting would be a check that cannot fail.

    Cannot arise on the real population (279 symbols), but a future
    reader hitting ok=False on a small slice should find this here
    rather than suspect a bug.
    """
    rows = [("A", 1.0, False), ("A", 1.0, True),
            ("B", 5.0, False), ("B", 5.0, True)]
    assert cluster_robust_contrast(rows)["ok"] is False


def _uncorrected_var(res: dict) -> float:
    """Strip the CR1 finite-sample factor, leaving only the sandwich.

    Needed because CR1 depends on G alone, so any comparison between a
    clustered and a singleton fit is partly a comparison of correction
    factors. Dividing it out isolates the meat.
    """
    g = res["clusters"]
    n = res["n_lo"] + res["n_hi"]
    cr1 = (g / (g - 1.0)) * ((n - 1.0) / (n - 2.0))
    return res["se"] ** 2 / cr1


def test_clustering_inflates_the_se_when_treatment_correlates_with_cluster() -> None:
    """THE REASON THE PROBE CLUSTERS AT ALL -- and the mechanism matters.

    Clustering does NOT inflate merely because rows within a cluster
    resemble each other. It inflates when TREATMENT IS CORRELATED WITH
    CLUSTER, because the contrast is then carried by differences BETWEEN
    clusters and there are only G of those, not N.

    That is exactly the real situation: extreme score deciles concentrate
    in particular symbols, so "top decile vs bottom decile" is partly
    "these symbols vs those symbols". Here each cluster is wholly
    treated or wholly control.

    TWO EARLIER VERSIONS OF THIS TEST WERE WRONG, both recorded because
    the mistakes are the instructive part:

    1. The first used within-cluster agreement with treatment BALANCED
       inside each cluster. The clustered SE came out SMALLER --
       correctly, since the per-cluster contrast is then very precisely
       estimated. It asserted something false about the estimator.
    2. The second asserted only `clustered_se > singleton_se`. The CR1
       finite-sample factor alone produces that (G=4 gives 1.5556
       against G=8's 1.3333), so the test PASSED on the very mutation it
       exists to catch -- the meat keyed by row instead of by cluster.

    So the assertion is on the RAW sandwich variances with CR1 divided
    out, against hand-derived values:

        singleton meat = SS0/N0^2 + SS1/N1^2 = 17/16 + 37/16 = 3.375
        clustered meat = 104/16 - 2*(1/4)*(1/2)*72 + (1/4)*72 = 6.5

    MUTATION RUN: key the meat by row instead of cluster id -> red
    (both variances become 3.375, so the ratio collapses to 1.0).
    MUTATION RUN: cross term loses its 2.0 -> red.
    """
    singleton = [("s0", 1.0, False), ("s1", 2.0, False),
                 ("s2", 5.0, False), ("s3", 6.0, False),
                 ("s4", 3.0, True), ("s5", 4.0, True),
                 ("s6", 9.0, True), ("s7", 10.0, True)]
    grouped = [("A", 1.0, False), ("A", 2.0, False),
               ("B", 5.0, False), ("B", 6.0, False),
               ("C", 3.0, True), ("C", 4.0, True),
               ("D", 9.0, True), ("D", 10.0, True)]

    a, b = cluster_robust_contrast(singleton), cluster_robust_contrast(grouped)
    assert a["ok"] and b["ok"]
    assert math.isclose(a["diff"], b["diff"]), "the point estimate must not move"

    va, vb = _uncorrected_var(a), _uncorrected_var(b)
    assert math.isclose(va, 3.375, rel_tol=1e-9), va
    assert math.isclose(vb, 6.5, rel_tol=1e-9), vb
    # the keying alone, with CR1 removed from both sides
    assert vb / va > 1.5, f"meat keying produced only {vb / va:.4f}x"
    assert b["se"] > a["se"]
    assert design_effect(b["se"], a["se"]) > 1.0


def test_cluster_robust_refuses_a_single_cluster() -> None:
    """One cluster carries no between-cluster information, so there is
    nothing to estimate. Returning a number here would be a check that
    cannot fail.

    MUTATION: drop the `g < 2` guard -> this returns ok=True with a
    meaningless SE -> red.
    """
    rows = [("only", 1.0, False), ("only", 2.0, False),
            ("only", 3.0, True), ("only", 4.0, True)]
    assert cluster_robust_contrast(rows)["ok"] is False


def test_cluster_robust_refuses_undersized_groups() -> None:
    rows = [("a", 1.0, False), ("b", 2.0, True), ("c", 3.0, True)]
    assert cluster_robust_contrast(rows)["ok"] is False


def test_design_effect_is_one_for_identical_ses() -> None:
    assert math.isclose(design_effect(0.25, 0.25), 1.0)
    assert math.isclose(design_effect(0.50, 0.25), 4.0)


# --- the bar ------------------------------------------------------------

def test_corrected_bar_is_stricter_than_uncorrected() -> None:
    """14 comparisons must make the bar HARDER.

    MUTATION: BAR_SIGMA_CORRECTED = 2.5 -> red.
    """
    assert BAR_SIGMA_CORRECTED > BAR_SIGMA


def test_bar_is_stricter_than_phase_a_scans_twelve_comparison_bar() -> None:
    """This run adds two comparisons to #589's twelve, so its bar must be
    strictly above 3.688.

    MUTATION: BAR_SIGMA_CORRECTED = 3.688 -> red.
    """
    assert BAR_SIGMA_CORRECTED > 3.688


def test_suggestive_band_is_not_a_pass() -> None:
    """3.0-3.729 must read SUGGESTIVE and say so.

    MUTATION: remove the SUGGESTIVE branch -> red.
    """
    v = verdict(3.2)
    assert v.startswith("SUGGESTIVE")
    assert "do not announce" in v
    assert not v.startswith("PASS")


def test_pass_requires_the_corrected_bar() -> None:
    assert verdict(3.8).startswith("PASS")
    assert verdict(2.9) == "FAIL"


def test_verdict_is_sign_agnostic() -> None:
    """A wrong-sign finding is still a finding -- A0's in-band MAE_R
    already reads +3.02 sigma in the wrong direction.

    MUTATION: compare sigma instead of abs(sigma) -> red.
    """
    assert verdict(-3.8).startswith("PASS")


def test_verdict_handles_nan() -> None:
    assert verdict(float("nan")) == "INSUFFICIENT"


# --- geometry -----------------------------------------------------------

def test_r_unit_applies_the_five_percent_cap() -> None:
    """R = min(1.5*atr, 0.05*entry). On a high-vol small cap the cap, not
    the ATR, sets R -- and getting this wrong rescales every MFE_R.

    MUTATION: drop the min() and return SL_ATR_MULT*atr -> red.
    """
    # ATR is 10% of price: 1.5*atr = 15% >> the 5% cap
    assert math.isclose(r_unit(100.0, 10.0), 5.0)
    # ATR is 1% of price: 1.5*atr = 1.5% < cap, so ATR governs
    assert math.isclose(r_unit(100.0, 1.0), 1.5)
    assert SL_ATR_MULT == 1.5 and SL_CAP_FRAC == 0.05


def test_true_ranges_match_the_engines_prev_close_convention() -> None:
    """predictor._atr prepends c[0] as the first prev_close, so the first
    TR uses the bar's own close. Reproduced, not approximated.

    MUTATION: use bars[i].close as prev_close for every i -> red.
    """
    bars = [_bar(0, 10.0, 9.0, 9.5), _bar(1, 12.0, 11.0, 11.5)]
    trs = true_ranges(bars)
    assert math.isclose(trs[0], 1.0)            # max(1, |10-9.5|, |9-9.5|)
    # bar 1 vs prev_close 9.5: max(1, |12-9.5|, |11-9.5|) = 2.5
    assert math.isclose(trs[1], 2.5)


def test_atr_needs_a_full_period_and_returns_none_otherwise() -> None:
    """A zero here would make R zero and MFE_R infinite, so insufficient
    history must drop the row rather than default.

    MUTATION: return 0.0 instead of None -> red.
    """
    trs = [1.0] * (ATR_PERIOD - 1)
    assert atr_at(trs, ATR_PERIOD - 2) is None
    full = [2.0] * ATR_PERIOD
    assert math.isclose(atr_at(full, ATR_PERIOD - 1), 2.0)


def test_atr_is_a_simple_mean_not_wilder() -> None:
    """MUTATION: replace with a Wilder/EMA recursion -> red."""
    trs = list(range(1, ATR_PERIOD + 1))  # 1..14
    expected = sum(trs) / ATR_PERIOD
    assert math.isclose(atr_at([float(x) for x in trs], ATR_PERIOD - 1), expected)


# --- excursions over windows FIXED BY DESIGN ----------------------------

def _ramp(n: int) -> list[Bar]:
    return [_bar(i * 3_600_000, 100.0 + i, 100.0 - i, 100.0) for i in range(n)]


def test_excursions_exclude_the_entry_bar_itself() -> None:
    """Entry is the signal bar's CLOSE, so the excursion window starts at
    the NEXT bar. Including the entry bar would leak the bar whose close
    was the entry.

    MUTATION: slice from entry_idx instead of entry_idx+1 -> red.
    """
    bars = [_bar(0, 999.0, 1.0, 100.0)] + [
        _bar(i * 3_600_000, 101.0, 99.0, 100.0) for i in range(1, 50)
    ]
    mfe, mae = excursions_at_horizons(bars, 0, 100.0, 1.0, (5,))
    assert math.isclose(mfe[5], 1.0)   # 101-100 over R=1, NOT 999
    assert math.isclose(mae[5], 1.0)


def test_excursions_grow_with_the_horizon_and_are_nested() -> None:
    """Nested windows by construction -- the reason agreement across
    horizons is ONE observation, not four."""
    mfe, _ = excursions_at_horizons(_ramp(60), 0, 100.0, 1.0, HORIZONS)
    vals = [mfe[h] for h in HORIZONS]
    assert vals == sorted(vals)


def test_truncated_horizons_are_omitted_not_shortened() -> None:
    """A partial window understates MFE and would bias the newest rows
    downward.

    MUTATION: clamp `last` to len(bars)-1 instead of skipping -> red.
    """
    mfe, _ = excursions_at_horizons(_ramp(12), 0, 100.0, 1.0, HORIZONS)
    assert 5 in mfe and 10 in mfe
    assert 20 not in mfe and 40 not in mfe


def test_zero_r_yields_no_excursions() -> None:
    mfe, mae = excursions_at_horizons(_ramp(60), 0, 100.0, 0.0, HORIZONS)
    assert mfe == {} and mae == {}


def test_mae_is_reported_as_a_positive_magnitude() -> None:
    """MUTATION: return (low - entry)/r -> red (sign flips)."""
    bars = [_bar(0, 100.0, 100.0, 100.0)] + [
        _bar(i * 3_600_000, 100.0, 95.0, 100.0) for i in range(1, 10)
    ]
    _, mae = excursions_at_horizons(bars, 0, 100.0, 1.0, (5,))
    assert mae[5] > 0


# --- the non-overlapping replication subset -----------------------------

def _row(sym: str, hour: int, score: float = 0.5) -> Row:
    return Row(symbol=sym, ts_ms=hour * 3_600_000, day="2026-10-01",
               score=score, confidence=1.0, became_trade=False)


def test_thinning_keeps_only_non_overlapping_windows() -> None:
    """Consecutive hourly rows share bars, so at horizon 10 only every
    10th survives.

    MUTATION: use `>` 0 instead of `>= span` -> red.
    """
    rows = [_row("AAA", h) for h in range(25)]
    kept = thin_non_overlapping(rows, 10)
    hours = sorted(r.ts_ms // 3_600_000 for r in kept)
    assert hours == [0, 10, 20]


def test_thinning_is_per_symbol_not_global() -> None:
    """Two symbols at the same timestamps do not overlap each other --
    thinning globally would discard half the data for no reason.

    MUTATION: drop the per-symbol grouping -> red.
    """
    rows = [_row("AAA", h) for h in range(10)] + [_row("BBB", h) for h in range(10)]
    kept = thin_non_overlapping(rows, 10)
    assert {r.symbol for r in kept} == {"AAA", "BBB"}
    assert len(kept) == 2


def test_thinning_is_deterministic_and_outcome_blind() -> None:
    """Selection must not depend on the value being measured, or the
    replication subset is selected on the outcome."""
    a = [_row("AAA", h, score=0.9) for h in range(30)]
    b = [_row("AAA", h, score=0.1) for h in range(30)]
    assert [r.ts_ms for r in thin_non_overlapping(a, 10)] == \
           [r.ts_ms for r in thin_non_overlapping(b, 10)]


# --- naive contrast, kept only for the comparison -----------------------

def test_naive_contrast_matches_the_textbook_two_sample_se() -> None:
    lo = [1.0, 2.0, 3.0, 4.0]
    hi = [3.0, 4.0, 5.0, 6.0]
    nv = naive_contrast(lo, hi)
    import statistics as st
    expected = math.sqrt(st.stdev(lo) ** 2 / 4 + st.stdev(hi) ** 2 / 4)
    assert math.isclose(nv["se"], expected)
    assert math.isclose(nv["diff"], 2.0)


def test_nominated_horizon_is_one_of_the_measured_horizons() -> None:
    """Guards the config itself: nominating a horizon the probe never
    computes would make the primary silently empty."""
    assert NOMINATED_HORIZON in HORIZONS
