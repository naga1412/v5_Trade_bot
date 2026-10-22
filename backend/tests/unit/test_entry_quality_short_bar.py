"""The SHORT-side score bar — MIN_ENTRY_SCORE_SHORT.

Before 2026-10-21 `_check_min_score` returned early for any direction
that was not LONG, so shorts faced no score bar at all. Harmless while
DISABLE_SHORT_SIGNALS is True; the dominant fact the moment it is
flipped. These tests pin the new behaviour AND pin that the default is
off, so merging this changes nothing until the setting is set.

Each mutation named below was run and confirmed to turn the test red.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.gates.entry_quality import _check_min_score


def _settings(**kw):
    base = {
        "MIN_ENTRY_SCORE_LONG": 0.36,
        "MIN_ENTRY_SCORE_SHORT": None,
        "PATTERN_BOOST_ENABLED": False,
        "PATTERN_BOOST_MIN_CONFIDENCE": 0.6,
        "PATTERN_BOOST_AMOUNT": 0.10,
        "PATTERN_PENALTY_AMOUNT": 0.15,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _sig(entry_score: float, **kw):
    base = {"entry_score": entry_score, "layer2_direction": None,
            "layer2_confidence": 0.0}
    base.update(kw)
    return SimpleNamespace(**base)


# --- the default must be inert ------------------------------------------

def test_the_real_settings_default_leaves_shorts_ungated() -> None:
    """IDENTITY AGAINST THE REAL Settings CLASS, not a hand-built stub.

    This is the property that makes the PR safe to merge ahead of any env
    change, so it must be asserted against the actual default rather than
    against a namespace the test wrote itself.

    The first version of this test used _settings(), which hard-codes
    MIN_ENTRY_SCORE_SHORT=None -- so defaulting the setting to 0.36 in
    config.py left it GREEN. Caught by mutation, not by review. A test
    that supplies the value it claims to be checking cannot check it.

    MUTATION: default MIN_ENTRY_SCORE_SHORT to 0.36 in config.py -> red.
    """
    from app.config import Settings

    assert Settings.model_fields["MIN_ENTRY_SCORE_SHORT"].default is None


def test_unset_bar_leaves_shorts_ungated() -> None:
    """With the setting unset, a short that would fail a 0.36 bar is not
    denied here -- today's behaviour, preserved."""
    res = _check_min_score("SHORT", _sig(-0.20), _settings())
    assert res.evaluated is False
    assert res.blocked is False


# --- the bar, once set ---------------------------------------------------

def test_short_below_the_bar_is_denied_on_magnitude() -> None:
    """A short's score is negative; conviction is its magnitude.

    -0.20 has magnitude 0.20 < 0.36 => denied.

    MUTATION: compare entry_score instead of -entry_score -> every short
    reads as far below the bar and the PASS test below goes red.
    """
    res = _check_min_score("SHORT", _sig(-0.20), _settings(MIN_ENTRY_SCORE_SHORT=0.36))
    assert res.blocked is True
    assert res.legacy_reason == "below_short_threshold"


def test_short_above_the_bar_passes() -> None:
    """-0.42 has magnitude 0.42 >= 0.36 => allowed.

    The positive control. Without it, a bar that denied every short
    would pass the test above while silently blocking the whole
    direction.

    MUTATION: `magnitude < threshold` -> `<=` leaves this green, so the
    boundary is covered separately below.
    """
    res = _check_min_score("SHORT", _sig(-0.42), _settings(MIN_ENTRY_SCORE_SHORT=0.36))
    assert res.blocked is False


def test_the_bar_is_inclusive_at_the_boundary() -> None:
    """Exactly at the threshold must PASS, matching the LONG side's
    `effective_score < threshold` denial test.

    MUTATION: `magnitude <= threshold` -> red.
    """
    res = _check_min_score("SHORT", _sig(-0.36), _settings(MIN_ENTRY_SCORE_SHORT=0.36))
    assert res.blocked is False


def test_long_side_is_unchanged_by_the_short_bar() -> None:
    """Regression guard: adding the SHORT branch must not alter LONG.

    MUTATION: route LONG through the short helper -> red.
    """
    allowed = _check_min_score("LONG", _sig(0.40), _settings())
    denied = _check_min_score("LONG", _sig(0.20), _settings())
    assert allowed.blocked is False
    assert denied.blocked is True
    assert denied.legacy_reason == "below_long_threshold"


def test_neutral_direction_is_still_not_applicable() -> None:
    res = _check_min_score("NEUTRAL", _sig(0.0), _settings(MIN_ENTRY_SCORE_SHORT=0.36))
    assert res.evaluated is False


def test_missing_entry_score_fails_open() -> None:
    res = _check_min_score(
        "SHORT", _sig(None), _settings(MIN_ENTRY_SCORE_SHORT=0.36),
    )
    assert res.blocked is False


# --- pattern boost / penalty, in magnitude terms ------------------------

def test_l2_agreement_can_lift_a_short_over_the_bar() -> None:
    """L2 agreeing with a short strengthens it; in magnitude that is
    +boost. 0.30 + 0.10 = 0.40 >= 0.36 => allowed.

    MUTATION: subtract the boost instead of adding -> red.
    """
    sig = _sig(-0.30, layer2_direction="SHORT", layer2_confidence=0.9)
    res = _check_min_score(
        "SHORT", sig,
        _settings(MIN_ENTRY_SCORE_SHORT=0.36, PATTERN_BOOST_ENABLED=True),
    )
    assert res.blocked is False


def test_l2_opposition_can_push_a_short_under_the_bar() -> None:
    """0.40 - 0.15 = 0.25 < 0.36 => denied, with the penalty named.

    MUTATION: add the penalty instead of subtracting -> red.
    """
    sig = _sig(-0.40, layer2_direction="LONG", layer2_confidence=0.9)
    res = _check_min_score(
        "SHORT", sig,
        _settings(MIN_ENTRY_SCORE_SHORT=0.36, PATTERN_BOOST_ENABLED=True),
    )
    assert res.blocked is True
    assert "L2_penalty" in (res.legacy_reason or "")
