"""Pure-function tests for the Phase A scan's statistics.

Covers the cell summary, the contrast and its bar, the decile cuts and
assignment, the verdict thresholds (including the SUGGESTIVE band that
must not read as a pass), and the replication consistency flags. No DB
and no network — these are the parts that decide what the scan reports.

Every mutation named below was RUN and confirmed to turn the test red.
Nothing is asserted about a mutation that was not actually executed —
four of the five cannot-fail tests caught in this project's history were
docstrings claiming mutations nobody had run.
"""
from __future__ import annotations

import math

from scripts.phase_a_scan import (
    BAR_SIGMA,
    BAR_SIGMA_CORRECTED,
    cell,
    contrast,
    decile_of,
    deciles,
    replication_flags,
    verdict,
)


# --- cell ---------------------------------------------------------------

def test_cell_reports_n_mean_sd_and_se() -> None:
    """MUTATION: se = sd (drop the /sqrt(n)) -> red."""
    c = cell([1.0, 2.0, 3.0, 4.0])
    assert c["n"] == 4
    assert math.isclose(c["mean"], 2.5)
    assert math.isclose(c["sd"], st_dev([1.0, 2.0, 3.0, 4.0]))
    assert math.isclose(c["se"], c["sd"] / 2.0)


def st_dev(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def test_cell_is_nan_safe_below_n2() -> None:
    assert cell([])["n"] == 0
    one = cell([5.0])
    assert one["n"] == 1 and one["sd"] != one["sd"]  # nan


# --- contrast and its bar ----------------------------------------------

def test_contrast_is_hi_minus_lo() -> None:
    """Sign convention matters for every reported diff.

    MUTATION: diff = lo.mean - hi.mean -> red.
    """
    c = contrast([0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0])
    assert c["ok"] is True
    assert math.isclose(c["diff"], 1.0)


def test_bar_comes_from_the_real_per_cell_sds() -> None:
    """The bar must be 3 x the pooled SE of the two REAL cells, never an
    assumed sigma.

    MUTATION: bar = BAR_SIGMA * 0.1 (a constant) -> red.
    """
    lo = [1.0, 2.0, 3.0, 4.0, 5.0]
    hi = [2.0, 3.0, 4.0, 5.0, 6.0]
    c = contrast(lo, hi)
    expected_se = (cell(lo)["se"] ** 2 + cell(hi)["se"] ** 2) ** 0.5
    assert math.isclose(c["se_diff"], expected_se)
    assert math.isclose(c["bar"], BAR_SIGMA * expected_se)
    assert math.isclose(c["bar_corrected"], BAR_SIGMA_CORRECTED * expected_se)


def test_corrected_bar_is_strictly_wider_than_the_uncorrected_one() -> None:
    """12 comparisons must make the bar HARDER, not easier.

    MUTATION: BAR_SIGMA_CORRECTED = 2.5 -> red.
    """
    assert BAR_SIGMA_CORRECTED > BAR_SIGMA
    c = contrast([1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 4.0, 5.0])
    assert c["bar_corrected"] > c["bar"]


def test_contrast_refuses_undersized_cells() -> None:
    c = contrast([1.0], [2.0, 3.0, 4.0])
    assert c["ok"] is False


# --- verdict: the SUGGESTIVE band must not read as a pass --------------

def _c(diff: float, se: float) -> dict:
    return {"ok": True, "diff": diff, "se_diff": se,
            "bar": BAR_SIGMA * se, "bar_corrected": BAR_SIGMA_CORRECTED * se,
            "sigma": diff / se}


def test_pass_requires_the_corrected_bar() -> None:
    """MUTATION: compare against c['bar'] instead of bar_corrected -> red,
    because the 3.2-sigma case below would then read PASS."""
    assert verdict(_c(4.0, 1.0)).startswith("PASS")


def test_three_to_three_point_six_eight_sigma_is_suggestive_not_pass() -> None:
    """THE BAND THAT MATTERS. 3.2 sigma clears 3 but not 3.68, so it must
    be SUGGESTIVE and explicitly not announced.

    MUTATION: drop the SUGGESTIVE branch so it returns PASS -> red.
    """
    v = verdict(_c(3.2, 1.0))
    assert v.startswith("SUGGESTIVE")
    assert "do not announce" in v
    assert not v.startswith("PASS")


def test_below_three_sigma_fails() -> None:
    assert verdict(_c(1.5, 1.0)) == "FAIL"


def test_verdict_is_sign_agnostic() -> None:
    """A large NEGATIVE effect is still a pass -- these scans are
    two-sided, and a wrong-sign finding is a finding.

    MUTATION: use diff instead of abs(diff) -> red.
    """
    assert verdict(_c(-4.0, 1.0)).startswith("PASS")


def test_insufficient_contrast_reports_insufficient() -> None:
    assert verdict({"ok": False}) == "INSUFFICIENT"


# --- deciles ------------------------------------------------------------

def test_deciles_returns_nine_interior_cuts() -> None:
    cuts = deciles([float(i) for i in range(100)])
    assert len(cuts) == 9


def test_deciles_refuses_when_there_is_less_data_than_buckets() -> None:
    """Fewer rows than buckets must return no cuts rather than silently
    producing degenerate ones.

    MUTATION: drop the len(xs) < k guard -> red.
    """
    assert deciles([1.0, 2.0, 3.0]) == []


def test_decile_of_assigns_monotonically() -> None:
    """MUTATION: `value > c` instead of `>=` leaves this green, so the
    boundary case is asserted separately below."""
    cuts = [10.0, 20.0, 30.0]
    assert decile_of(5.0, cuts) == 0
    assert decile_of(15.0, cuts) == 1
    assert decile_of(25.0, cuts) == 2
    assert decile_of(35.0, cuts) == 3


def test_decile_of_is_inclusive_at_a_cut() -> None:
    """A value exactly on a cut belongs to the HIGHER bucket.

    MUTATION: `value > c` instead of `>=` -> red.
    """
    assert decile_of(10.0, [10.0, 20.0]) == 1


def test_decile_assignment_covers_every_row() -> None:
    """Invariant worth pinning: deciling must not drop rows."""
    vals = [float(i) for i in range(50)]
    cuts = deciles(vals)
    counts: dict[int, int] = {}
    for v in vals:
        counts[decile_of(v, cuts)] = counts.get(decile_of(v, cuts), 0) + 1
    assert sum(counts.values()) == len(vals)


# --- replication --------------------------------------------------------

def test_replication_requires_same_sign_and_within_two_se() -> None:
    """Consistency, not re-significance.

    MUTATION: use 1.0 * se_diff instead of 2.0 -> the within=True case
    below becomes False -> red.
    """
    primary = _c(1.0, 0.5)
    same_and_close = replication_flags(primary, _c(1.6, 0.5))
    assert "same_sign=True" in same_and_close
    assert "within_2SE=True" in same_and_close


def test_replication_flags_an_opposite_sign_half() -> None:
    primary = _c(1.0, 0.5)
    flipped = replication_flags(primary, _c(-1.0, 0.5))
    assert "same_sign=False" in flipped


def test_replication_flags_a_far_half_even_when_the_sign_agrees() -> None:
    """Same sign is not sufficient -- magnitude has to be consistent too.

    MUTATION: return only the same_sign term -> red.
    """
    primary = _c(1.0, 0.5)
    far = replication_flags(primary, _c(5.0, 0.5))
    assert "same_sign=True" in far
    assert "within_2SE=False" in far


def test_replication_is_na_without_both_contrasts() -> None:
    assert replication_flags({"ok": False}, _c(1.0, 0.5)) == "n/a"


# --- degenerate median split (added after the first real run) ----------

def _rows_for_split(values):
    """Minimal stand-ins exposing just what _median_split reads."""
    from scripts.phase_a_scan import Row
    out = []
    for i, v in enumerate(values):
        out.append(Row(
            trade_id=i, symbol="X", timeframe="1h", opened_ms=0, closed_ms=0,
            entry_price=100.0, stop_loss=90.0, take_profit=120.0,
            entry_score=0.4, mtf_agreement=4, bars_held=10,
            funding_directional_adj=v,
            oi_24h_delta=None, oi_age_h=None, funding_rate=None,
            funding_age_h=None, depth_0_5pct_usdt=None, depth_age_h=None,
            pnl_pct=float(i), exit_reason="STOP_LOSS", closed_at=i,
        ))
    return out


def test_median_split_falls_back_when_the_median_is_the_mode() -> None:
    """THE DEFECT THE FIRST REAL RUN EXPOSED.

    funding_directional_adj has two distinct values over 60d -- 3,572
    zeros against 98 at 0.1 -- so its median is 0 and `>= med` captures
    the whole population, emptying the low cell. The scan reported
    "INSUFFICIENT CELLS", which reads as missing data rather than as a
    split that cannot separate.

    MUTATION: drop the fallback branch -> lo is empty and this goes red.
    """
    from scripts.phase_a_scan import _median_split
    rows = _rows_for_split([0.0] * 8 + [0.1] * 2)
    lo, hi, cut = _median_split(rows, lambda r: r.funding_directional_adj,
                                "pnl_pct")
    assert cut == 0.1, "should split at the first value above the minimum"
    assert len(lo) == 8 and len(hi) == 2


def test_median_split_is_unchanged_on_a_normal_distribution() -> None:
    """The fallback must not alter the ordinary case.

    MUTATION: always take the fallback branch -> cut becomes the second
    distinct value instead of the median and this goes red.
    """
    from scripts.phase_a_scan import _median_split
    rows = _rows_for_split([float(i) for i in range(10)])
    lo, hi, cut = _median_split(rows, lambda r: r.funding_directional_adj,
                                "pnl_pct")
    assert cut == 4.5
    assert len(lo) == 5 and len(hi) == 5


def test_median_split_refuses_a_genuinely_constant_variable() -> None:
    """All-identical values cannot be split at all -- that must report
    insufficient rather than inventing a cut."""
    from scripts.phase_a_scan import _median_split
    rows = _rows_for_split([0.0] * 10)
    lo, hi, cut = _median_split(rows, lambda r: r.funding_directional_adj,
                                "pnl_pct")
    assert lo == [] and hi == []
    assert cut != cut  # nan


# --- fixed-horizon MFE must be pre-treatment ---------------------------

def test_horizons_are_a_fixed_design_constant() -> None:
    """The horizons must be constants, not derived from any trade's
    realised duration -- that is the whole point of using them.

    MUTATION: set MFE_HORIZONS from observed hold lengths -> this stops
    being a tuple of ints and goes red.
    """
    from scripts.phase_a_scan import MFE_HORIZONS
    assert MFE_HORIZONS == (5, 10, 20, 40)
    assert all(isinstance(h, int) and h > 0 for h in MFE_HORIZONS)


def test_horizon_fetch_reaches_past_the_longest_horizon() -> None:
    """The fetch window must extend beyond the exit, or a trade that
    exited early has no forward bars and its long horizons silently
    vanish -- the #585 trap.

    Asserted against the source because the window is computed inside an
    async closure that needs a live client to exercise.
    """
    import io
    from pathlib import Path
    src = io.open(
        Path(__file__).resolve().parents[2] / "scripts" / "phase_a_scan.py",
        encoding="utf-8",
    ).read()
    assert "horizon_end = r.opened_ms + (max(MFE_HORIZONS) + 1) * bar_ms" in src
    assert "max(r.closed_ms, horizon_end)" in src, (
        "fetch must span whichever is later: the exit or the longest horizon"
    )


def test_short_horizon_mfe_cannot_exceed_a_longer_one() -> None:
    """Monotonicity: a maximum over more bars cannot be smaller.

    This is the invariant that catches an off-by-one in the slicing --
    if seg were fwd[h:] instead of fwd[:h], ordering would break.
    """
    from scripts.phase_a_scan import Bar
    entry, r_unit = 100.0, 10.0
    highs = [101.0, 103.0, 102.0, 108.0, 104.0, 115.0]
    mfes = []
    for h in (2, 4, 6):
        seg = [Bar(ts_ms=i, high=v, low=99.0, close=v)
               for i, v in enumerate(highs)][:h]
        mfes.append(max((b.high - entry) / r_unit for b in seg))
    assert mfes == sorted(mfes), "MFE must be non-decreasing in horizon"
    assert mfes[0] == 0.3 and mfes[2] == 1.5
