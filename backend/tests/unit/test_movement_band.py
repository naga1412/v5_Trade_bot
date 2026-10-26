"""Tests for the expected-movement card band.

The band is a VOLATILITY estimate, so most of these tests are about what
it must NOT say and where it must NOT appear. Assertions are against
independently-derived exact values wherever a value can be derived,
because an inequality can be satisfied by a mechanism other than the one
under test -- see
`docs/superpowers/decisions/2026-09-08-checks-that-cannot-fail.md`.

Every mutation named below was RUN, and each was checked against the
test it TARGETS rather than against the suite as a whole: a mutant
caught by some other test is a RED for the one it was aimed at, however
green the total looks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.telegram.movement_band import (
    BAND_R,
    CUT_HIGH,
    CUT_LOW,
    RENDER_COHORTS,
    SL_ATR_MULT,
    SL_CAP_FRAC,
    band_for,
    band_label,
    r_capped,
    render_band,
)


@dataclass
class _Cand:
    """Minimal stand-in; band_for reads by getattr, not by type."""

    direction: str = "LONG"
    timeframe: str = "1h"
    symbol_source: str = "established_top20"
    entry_score: float | None = 0.30
    entry_price: float = 100.0
    # 1h mean R/entry is 0.02378 (LONG shadow trades, n=1,164), so this
    # stop makes the fixture realistic rather than round.
    stop_loss_price: float = 97.622


# --- the omission conditions: each one deliberate -----------------------

def test_omits_on_short() -> None:
    """The measurement is LONG-only and a SHORT stream went live
    2026-10-23, so this is a live consumer rather than a hypothetical. A
    band derived from LONG data on a SHORT card would be an
    extrapolation presented as a measurement.

    MUTATION RUN: dropped the direction check -> red.
    """
    assert band_for(_Cand(direction="SHORT")) is None


def test_omits_on_futures_poll_cohort() -> None:
    """FU-53 excluded futures_poll from the measurement entirely -- 60 of
    279 symbols dropped by a spot-klines fetch -- so there is no evidence
    for those symbols.

    MUTATION RUN: added "futures_poll" to RENDER_COHORTS -> red.
    """
    assert band_for(_Cand(symbol_source="futures_poll")) is None
    # and the two measured cohorts DO render, or the check above would
    # pass trivially by omitting everything
    for cohort in RENDER_COHORTS:
        assert band_for(_Cand(symbol_source=cohort)) is not None


def test_omits_on_15m_an_unreachable_branch_guarded_deliberately() -> None:
    """A CURRENTLY UNREACHABLE BRANCH, ASSERTED ON PURPOSE.

    All three dispatch spawn paths are hardcoded to 1h -- `main.py`'s
    `start_background_worker()` call takes no arguments so
    `run_live_prediction`'s `timeframe: str = "1h"` default wins,
    `keepalive.KEEPALIVE_TIMEFRAME = "1h"`, and
    `futures_poll.FUTURES_POLL_TIMEFRAME = "1h"`. None is settings-driven,
    so this cannot drift via `.env`; only a code change can make 15m
    reachable.

    The guard exists so that such a change FAILS LOUDLY here rather than
    silently widening the band's reach to a lane whose ATR is roughly
    half what the band was measured on. That substitution is exactly the
    pooled-rate error one layer over, and it would ship a wrong number
    rather than no number.

    MUTATION RUN: dropped the timeframe check -> red.
    """
    assert band_for(_Cand(timeframe="15m")) is None


def test_omits_when_score_is_missing() -> None:
    """No score, no decile. MUTATION RUN: defaulted a missing score to
    0.0 -> red (0.0 is below CUT_LOW, so it would render a band)."""
    assert band_for(_Cand(entry_score=None)) is None


def test_omits_on_unusable_geometry_rather_than_fabricating() -> None:
    """A non-positive R cannot be converted, and a placeholder would be a
    fabricated number on a live card.

    MUTATION RUN: returned 0.0 instead of None from r_capped -> red.
    """
    assert band_for(_Cand(stop_loss_price=100.0)) is None   # R == 0
    assert band_for(_Cand(stop_loss_price=101.0)) is None   # R < 0
    assert band_for(_Cand(entry_price=0.0)) is None


def test_no_fallback_text_when_omitted() -> None:
    """A missing field is informative; a placeholder is not.

    MUTATION RUN: made render_band return a "n/a" line for None -> red.
    """
    assert render_band(None) == ""


# --- the boundaries, exact ---------------------------------------------

def test_band_label_boundaries_are_the_measured_cuts() -> None:
    """Cuts are the 10th/90th percentiles of `final_score` over 1h LONG
    `predictions`, 60d, n=44,498, snapshot 2026-10-23.

    MUTATION RUN: swapped `<` for `<=` on the low cut -> red on the
    CUT_LOW case below.
    MUTATION RUN: swapped `>=` for `>` on the high cut -> red on the
    CUT_HIGH case below.
    """
    assert math.isclose(CUT_LOW, 0.0675)
    assert math.isclose(CUT_HIGH, 0.2791)

    assert band_label(CUT_LOW - 1e-9) == "narrower than usual"
    assert band_label(CUT_LOW) == "usual"            # low cut is EXCLUSIVE below
    assert band_label(CUT_HIGH - 1e-9) == "usual"
    assert band_label(CUT_HIGH) == "wider than usual"  # high cut is INCLUSIVE


def test_the_three_labels_are_comparative_not_a_quality_ladder() -> None:
    """Ladder words ascend, and anything that ascends invites reading the
    top as the good end -- the "thinner liquidity, unvalidated"
    mislabelling inverted. These must describe WIDTH.

    MUTATION RUN: renamed the top band to "high conviction" -> red.
    """
    labels = set(BAND_R)
    assert labels == {"narrower than usual", "usual", "wider than usual"}
    banned = ("strong", "confidence", "conviction", "quality", "high", "low",
              "good", "better", "best", "elevated", "typical")
    for label in labels:
        for word in banned:
            assert word not in label.lower(), f"{label!r} contains {word!r}"


# --- the conversion, exact ---------------------------------------------

def test_r_capped_reconstructs_atr_exactly_then_reapplies_the_cap() -> None:
    """The live stop is uncapped, so `entry - stop` IS `1.5 * ATR` and
    ATR is that over 1.5 -- exact, because both paths share the
    multiplier and the `_atr` function. The cap is then re-applied so the
    band is denominated in the R its evidence was measured in.

    Derived by hand: entry 100, stop 97.622 -> R_live 2.378 ->
    ATR 1.58533... -> 1.5*ATR == 2.378, cap == 5.0, so min is 2.378.

    MUTATION RUN: dropped the min() so the cap never applies -> red on
    the cap-bound case below.
    """
    assert math.isclose(r_capped(100.0, 97.622), 2.378, rel_tol=1e-9)
    # cap-bound: a 10%-of-entry stop means 1.5*ATR == 10 > the 5.0 cap
    assert math.isclose(r_capped(100.0, 90.0), 5.0, rel_tol=1e-9)
    assert math.isclose(SL_ATR_MULT, 1.5) and math.isclose(SL_CAP_FRAC, 0.05)


def test_percent_conversion_is_per_trade_not_a_population_mean() -> None:
    """band_percent = band_R * R_capped / entry, using THIS trade's R.

    Using the population mean R/entry would reintroduce the pooled-rate
    substitution one layer down -- the error that produced 4.12% for a
    lane whose real rate is 10.48%.

    Two trades with the SAME score and DIFFERENT stop distances must get
    different percentages; a population-mean implementation would give
    them the same one.

    MUTATION RUN: replaced r_capped(...)/entry with the constant 0.02378
    -> red.
    """
    tight = band_for(_Cand(entry_score=0.35, stop_loss_price=99.0))
    wide = band_for(_Cand(entry_score=0.35, stop_loss_price=95.0))
    assert tight is not None and wide is not None
    assert tight.label == wide.label == "wider than usual"
    assert wide.adverse_pct > tight.adverse_pct
    # exact: R/entry is 0.01 and 0.05, and the top band's adverse R is 1.3309
    assert math.isclose(tight.adverse_pct, 1.3309 * 0.01 * 100.0, rel_tol=1e-9)
    assert math.isclose(wide.adverse_pct, 1.3309 * 0.05 * 100.0, rel_tol=1e-9)


def test_band_percentages_match_the_measured_extreme_deciles() -> None:
    """At the 1h lane's mean R/entry of 0.02378 the extreme deciles come
    out at the figures recorded in the scope doc.

    MUTATION RUN: swapped the favourable and adverse legs in BAND_R ->
    red (the favourable leg is the larger one).
    """
    lo = band_for(_Cand(entry_score=0.05))
    hi = band_for(_Cand(entry_score=0.35))
    assert lo is not None and hi is not None
    assert math.isclose(lo.favourable_pct, 3.03, abs_tol=0.01)
    assert math.isclose(lo.adverse_pct, 2.49, abs_tol=0.01)
    assert math.isclose(hi.favourable_pct, 3.87, abs_tol=0.01)
    assert math.isclose(hi.adverse_pct, 3.17, abs_tol=0.01)
    # the favourable leg exceeds the adverse one in EVERY band -- the
    # measured asymmetry is 1.24x, which is why they are not collapsed
    # into one symmetric +/- figure
    for fav, adv in BAND_R.values():
        assert fav > adv


# --- the rendered text -------------------------------------------------

def test_rendered_text_is_two_sided_and_never_prescribes_a_response() -> None:
    """The explicit +/- pair carries two-sidedness in the VALUE as well
    as the label, since a one-sided reading is the specific
    misinterpretation available here.

    And no response guidance: "Consider sizing down" was dropped because
    it prescribes one response among several when none was measured. A
    wider swing could rationally mean a smaller position, a wider stop,
    or skipping the trade -- and an operator sizing by fixed risk over
    stop distance already has an ATR-driven size a magnitude flag does
    not change.

    MUTATION RUN: re-added "Consider sizing down." -> red.
    MUTATION RUN: dropped the adverse leg, leaving one number -> red.
    """
    text = render_band(band_for(_Cand(entry_score=0.35)))
    assert "-3.2%" in text and "+3.9%" in text
    assert "wider than usual" in text
    assert "Volatility estimate, not conviction." in text
    for banned in ("sizing", "size down", "consider", "recommend", "should",
                   "strong", "confidence", "conviction score", "quality"):
        assert banned not in text.lower().replace(
            "volatility estimate, not conviction.", ""), banned


def test_rendered_text_never_prints_R() -> None:
    """R stays the internal unit. MAE_R runs 1.0461-1.3309 while the stop
    sits at exactly 1.0 R, so an R-denominated band printed beside a stop
    asserts something that cannot happen to the operator.

    MUTATION RUN: added the R values to the rendered line -> red.
    """
    text = render_band(band_for(_Cand(entry_score=0.35)))
    assert "R" not in text.replace("Price swing", "").replace(
        "Volatility estimate, not conviction.", "")


# --- the wiring, which is where this constructor has failed before ------

def test_dispatcher_passes_entry_score_to_the_candidate() -> None:
    """THE GUARD THAT MATTERS MOST, because this exact constructor has
    already shipped this exact bug.

    `SignalCandidate.entry_score` is DEFAULTED to None so legacy callers
    and fixtures keep working. A default means a caller that forgets to
    pass it silently omits the band -- and dispatcher.py's own comment
    records that four cohort fields "were NEVER passed, so
    SignalCandidate's own `symbol_source` default always won at render
    time and the cohort banner could not render on ANY real card -- for
    any cohort, ever... the data looked right while the card was
    silent."

    Asserting the band function works proves nothing about whether the
    band ever appears. This reads the dispatcher's source for the
    keyword, which is crude but checks the one thing a unit test of
    `band_for` cannot.

    MUTATION RUN: removed `entry_score=proposal.entry_score` from the
    dispatcher -> red.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "app" / "trading" / "execution" / "dispatcher.py").read_text(encoding="utf-8")
    start = src.index("candidate = SignalCandidate(")
    end = src.index("\n    )", start)
    block = src[start:end]
    assert "entry_score=proposal.entry_score" in block, (
        "dispatcher builds SignalCandidate without passing entry_score, so "
        "the band will silently never render -- the 2026-09-08 cohort-banner "
        "bug repeated"
    )


def test_payload_carries_entry_score_so_edits_keep_the_band() -> None:
    """The edit path reconstructs a SignalCandidate from the PERSISTED
    payload, so the band would vanish the first time a card is edited if
    the score were not persisted.

    MUTATION RUN: removed "entry_score" from build_signal_payload -> red.
    """
    from app.telegram.signals import SignalCandidate, build_signal_payload
    from datetime import datetime, timezone

    cand = SignalCandidate(
        signal_id="sig_x", symbol="BTC/USDT", timeframe="1h", direction="LONG",
        entry_price=100.0, stop_loss_price=97.622, take_profit_price=107.0,
        confidence_pct=70.0, layer_summary={}, margin_usdt=100.0,
        funding_rate_daily=0.0, chart_url="http://x", sl_distance_pct=0.0238,
        rr_ratio=3.0, entry_score=0.35,
    )
    payload = build_signal_payload(
        cand, rendered_at=datetime.now(timezone.utc),
        initial_leverage=3, hard_cap=5,
    )
    assert payload["entry_score"] == 0.35


def test_band_appears_in_a_rendered_card_and_is_absent_on_short() -> None:
    """End to end through the real renderer, because every assertion
    above could hold while the block never reached the body.

    MUTATION RUN: removed `{movement_block}` from the body template ->
    red.
    """
    from app.telegram.signals import SignalCandidate, render_message

    def _cand(**kw):
        base = dict(
            signal_id="sig_x", symbol="BTC/USDT", timeframe="1h",
            direction="LONG", entry_price=100.0, stop_loss_price=97.622,
            take_profit_price=107.0, confidence_pct=70.0, layer_summary={},
            margin_usdt=100.0, funding_rate_daily=0.0, chart_url="http://x",
            sl_distance_pct=0.0238, rr_ratio=3.0, entry_score=0.35,
        )
        base.update(kw)
        return SignalCandidate(**base)

    body = render_message(_cand(), leverage=3, auto_skip_seconds=600).body
    assert "Price swing, next ~10h" in body
    assert "wider than usual" in body
    assert "Volatility estimate, not conviction." in body

    short_body = render_message(
        _cand(direction="SHORT"), leverage=3, auto_skip_seconds=600).body
    assert "Price swing" not in short_body, (
        "the band rendered on a SHORT card -- it is LONG-only evidence"
    )


def test_adverse_leg_leads() -> None:
    """THE ORDER IS LOAD-BEARING, so a test holds it.

    Favourable-first puts the LARGER number first, and the larger number
    is the WEAKER evidence: the favourable leg read +3.69 sigma and did
    not pass the corrected 3.729 bar, while the adverse leg read +8.62
    and did. So a quick read of "+3.9% / -3.2%" is "more upside than
    downside" -- the exact conviction misreading this field exists to
    prevent, delivered by the ordering alone. The adverse leg is also
    the sizing-relevant one, being what reaches the stop.

    An unexplained order invites someone to "fix" it back to the
    more natural-looking ascending form, which is why this is a test and
    not only a comment.

    MUTATION RUN: swapped the legs back to favourable-first -> red.
    """
    text = render_band(band_for(_Cand(entry_score=0.35)))
    adverse_at = text.index("-3.2%")
    favourable_at = text.index("+3.9%")
    assert adverse_at < favourable_at, (
        "the favourable leg leads, so a quick read says 'more upside than "
        "downside' -- the weaker-evidence number must not come first"
    )
