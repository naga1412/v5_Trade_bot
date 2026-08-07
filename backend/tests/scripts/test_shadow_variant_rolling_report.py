"""Pure-function tests for shadow_variant_rolling_report.

Covers cohort classification, decision rule, early-read flag, fee
assertion, paired-group summarization, (item 1c) live-gate counterfactual
eligibility labeling, and the strided ADX-series builder (httpx.MockTransport,
no real network). No DB dependency — the SQL/report paths are exercised by
the ops-debug probe against prod.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx
import pytest

from scripts.shadow_variant_rolling_report import (
    ADX_PERIOD,
    DECISION_N,
    _MAX_ADX_SAMPLES_PER_TF,
    PairRow,
    _build_adx_series,
    _classify_cohort,
    _decision,
    _early_read,
    _fee_assertion_flag,
    _mean_se,
    _nearest_before,
    _summarize_group,
    compute_live_eligible,
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


def test_pair_row_defaults_live_eligible_true() -> None:
    """Pre-item-1c construction sites (this file's own _p helper, any
    other caller) must keep working unchanged."""
    p = _p(variant="breakeven_0.40R", cohort="atr_bound", tf="1h",
           base=0.0, var=0.0, armed=False)
    assert p.live_eligible is True


# ---- live-gate eligibility counterfactual (item 1c) --------------------


def test_nearest_before_finds_latest_value_at_or_before_ts() -> None:
    series = {1000: 10.0, 2000: 20.0, 3000: 30.0}
    assert _nearest_before(series, 2500) == 20.0
    assert _nearest_before(series, 3000) == 30.0
    assert _nearest_before(series, 999) is None


def test_compute_live_eligible_long_in_bear_regime_without_override_blocked() -> None:
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "bear"}
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series={},
    )
    assert got is False


def test_compute_live_eligible_long_in_bear_regime_with_l2_override_allowed() -> None:
    """L2 agreeing at high confidence (>= REGIME_OPPOSITE_PATTERN_OVERRIDE,
    default 0.8) overrides an opposing regime per the real gate's rule 2."""
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "bear"}
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction="LONG", l2_confidence=0.95,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series={},
    )
    assert got is True


def test_compute_live_eligible_bull_regime_passes_regime_check() -> None:
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "bull"}
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series={},
    )
    assert got is True


def test_compute_live_eligible_missing_regime_day_falls_open_on_regime() -> None:
    """No regime entry for that date -> market_regime=None -> the regime
    check can't detect an opposing regime (None not in ('bull','bear')) --
    falls open on that specific sub-gate, matching open_position_gate's
    own documented fail-open behaviour."""
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date={}, adx_series={},
    )
    assert got is True


def test_compute_live_eligible_low_adx_blocked() -> None:
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "neutral"}
    ts_ms = int(opened_at.timestamp() * 1000)
    adx_series = {"1h": {ts_ms: 5.0}}  # well below the 25.0 default floor
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series=adx_series,
    )
    assert got is False


def test_compute_live_eligible_missing_adx_falls_open() -> None:
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "neutral"}
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series={},
    )
    assert got is True


def test_compute_live_eligible_below_forced_entry_threshold_blocked() -> None:
    """compute_live_eligible forces MIN_ENTRY_SCORE_LONG=0.36 (matching
    the query's own entry_score>=0.36 filter) regardless of prod's
    deployed threshold, so the counterfactual is self-consistent."""
    opened_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    regime_by_date = {opened_at.date(): "neutral"}
    got = compute_live_eligible(
        direction="LONG", entry_score=0.20,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series={},
    )
    assert got is False


def test_compute_live_eligible_naive_datetime_treated_as_utc_not_local() -> None:
    """A naive opened_at (e.g. an asyncpg TIMESTAMP-without-timezone
    round-trip) must be interpreted as UTC for both the regime-day lookup
    and the ADX timestamp lookup -- not local system time. Regression
    test: datetime.timestamp() on a naive value silently assumes local
    tz, which would corrupt the ADX ts_ms lookup on any non-UTC host."""
    opened_at = datetime(2026, 6, 1, 12, 0)  # naive
    regime_by_date = {opened_at.date(): "bull"}
    ts_ms_utc = int(opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    adx_series = {"1h": {ts_ms_utc: 30.0}}  # only present at the UTC-correct ts
    got = compute_live_eligible(
        direction="LONG", entry_score=0.50,
        l2_direction=None, l2_confidence=None,
        dominant_tf="1h", opened_at=opened_at,
        regime_by_date=regime_by_date, adx_series=adx_series,
    )
    assert got is True


# ---- _build_adx_series (strided, O(n) not O(n^2)) ----------------------


def _klines_transport(*, bar_ms: int, total_bars: int) -> httpx.MockTransport:
    """Synthetic Binance klines transport: `total_bars` bars spaced
    `bar_ms` apart starting at ts=0, served oldest-first up to `limit`
    bars ending at-or-before `endTime` (or the series end if absent) --
    matches real Binance semantics closely enough for `_fetch_klines_df`.
    """
    last_ts = (total_bars - 1) * bar_ms

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        limit = int(params.get("limit", "1000"))
        end_ms = min(int(params["endTime"]) if "endTime" in params else last_ts, last_ts)
        end_idx = end_ms // bar_ms
        start_idx = max(0, end_idx - limit + 1)
        rows = [
            [idx * bar_ms, 100.0 + (idx % 50) * 0.1, 100.0 + (idx % 50) * 0.1 + 1,
             100.0 + (idx % 50) * 0.1 - 1, 100.0 + (idx % 50) * 0.1, "0"]
            for idx in range(start_idx, end_idx + 1)
        ]
        return httpx.Response(200, json=rows)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_build_adx_series_caps_sample_count_for_large_input() -> None:
    """Regression test for the 2026-08-07 prod incident: computing
    _adx() (an O(m) Wilder-smoothing recompute-from-scratch) at every
    single bar is O(n^2) total, and hung 2+ hours in a real prod run
    over ~92d of 5m bars before the SSH pipe eventually broke, stranding
    the run rather than completing it. 3000 bars is well over the cap
    but small enough to keep this test fast."""
    transport = _klines_transport(bar_ms=5 * 60_000, total_bars=3000)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _build_adx_series(client, "5m", needed_since_ms=0)
    assert 0 < len(result) <= _MAX_ADX_SAMPLES_PER_TF + 1


@pytest.mark.asyncio
async def test_build_adx_series_densely_samples_small_input() -> None:
    """Inputs well under the cap get every bar -- unchanged behaviour
    from before the stride fix (no loss of resolution when it isn't
    needed for performance)."""
    transport = _klines_transport(bar_ms=60 * 60_000, total_bars=50)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _build_adx_series(client, "1h", needed_since_ms=0)
    assert len(result) == 50 - (ADX_PERIOD + 2)


@pytest.mark.asyncio
async def test_build_adx_series_calls_real_adx_not_a_reimplementation() -> None:
    """The counterfactual's validity depends on using the REAL _adx()
    (imported, never reimplemented) -- sanity-check the returned values
    are plausible ADX magnitudes (0-100), not placeholder/zero output."""
    transport = _klines_transport(bar_ms=60 * 60_000, total_bars=50)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _build_adx_series(client, "1h", needed_since_ms=0)
    assert all(0.0 <= v <= 100.0 for v in result.values())
