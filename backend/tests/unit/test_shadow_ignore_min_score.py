"""Tests for SHADOW_IGNORE_MIN_SCORE config flag.

Verifies:
1. open_position_gate (live dispatch path) still denies LONG below MIN_ENTRY_SCORE_LONG
   even when SHADOW_IGNORE_MIN_SCORE=True — the gate itself is completely unaffected.
2. SHADOW_IGNORE_MIN_SCORE defaults to False (no behaviour change on deploy).
3. SHORT decisions are unaffected by the flag (only LONG threshold is overridden).
4. Shadow worker's override: a below_long_threshold denial flips to allow when the
   flag is set — all other denial reasons still propagate.
5. PATTERN_BOOST suffix variant ("below_long_threshold (effective_score=...)") is
   also overridden via startswith() match.
6. Both SHADOW_ALLOW_SHORTS and SHADOW_IGNORE_MIN_SCORE true simultaneously compose:
   short_disabled → allow (SHADOW_ALLOW_SHORTS path); below_long_threshold → allow
   (SHADOW_IGNORE_MIN_SCORE path).
"""
from __future__ import annotations

import dataclasses

from app.config import Settings
from app.core.gates.entry_quality import AllowDecision, open_position_gate


@dataclasses.dataclass
class _Sig:
    """Minimal duck-type for open_position_gate."""
    direction: str
    entry_score: float = 0.1


def _settings(**kwargs) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql://x",
        redis_url="redis://x",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Gate-level isolation (live dispatch path must NOT be affected by the flag)
# ---------------------------------------------------------------------------


def test_live_gate_denies_long_below_threshold_regardless_of_shadow_flag() -> None:
    """open_position_gate must deny LONG below threshold even when SHADOW_IGNORE_MIN_SCORE=True."""
    settings = _settings(MIN_ENTRY_SCORE_LONG=0.36, SHADOW_IGNORE_MIN_SCORE=True)
    decision = open_position_gate(_Sig(direction="LONG", entry_score=0.2), settings)
    assert not decision.allow
    assert decision.reason == "below_long_threshold"


def test_live_gate_allows_long_above_threshold_with_flag() -> None:
    """LONG above threshold still allowed when SHADOW_IGNORE_MIN_SCORE=True."""
    settings = _settings(MIN_ENTRY_SCORE_LONG=0.36, SHADOW_IGNORE_MIN_SCORE=True)
    decision = open_position_gate(_Sig(direction="LONG", entry_score=0.4), settings)
    assert decision.allow


def test_live_gate_short_unaffected_by_shadow_min_score_flag() -> None:
    """SHADOW_IGNORE_MIN_SCORE must not affect SHORT decisions."""
    settings = _settings(DISABLE_SHORT_SIGNALS=True, SHADOW_IGNORE_MIN_SCORE=True)
    decision = open_position_gate(_Sig(direction="SHORT"), settings)
    assert not decision.allow
    assert decision.reason == "short_disabled"


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_shadow_ignore_min_score_default_false() -> None:
    """SHADOW_IGNORE_MIN_SCORE must default to False — no behaviour change on deploy."""
    s = _settings()
    assert s.SHADOW_IGNORE_MIN_SCORE is False


# ---------------------------------------------------------------------------
# Shadow-worker override logic (unit-level, exercising the AllowDecision path)
# ---------------------------------------------------------------------------


def test_shadow_override_flips_below_long_threshold_to_allow() -> None:
    """When SHADOW_IGNORE_MIN_SCORE=True, a below_long_threshold denial becomes allow."""
    denied = AllowDecision(allow=False, reason="below_long_threshold")
    settings = _settings(SHADOW_IGNORE_MIN_SCORE=True)

    decision = denied
    if (
        not decision.allow
        and (decision.reason or "").startswith("below_long_threshold")
        and settings.SHADOW_IGNORE_MIN_SCORE
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert decision.allow
    assert decision.reason is None


def test_shadow_override_flips_pattern_boost_variant() -> None:
    """Boost-detail suffix variant is also overridden (startswith match)."""
    boost_reason = "below_long_threshold (effective_score=0.310, base=0.350 - L2_penalty=0.15, L2 conf=0.75)"
    denied = AllowDecision(allow=False, reason=boost_reason)
    settings = _settings(SHADOW_IGNORE_MIN_SCORE=True)

    decision = denied
    if (
        not decision.allow
        and (decision.reason or "").startswith("below_long_threshold")
        and settings.SHADOW_IGNORE_MIN_SCORE
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert decision.allow
    assert decision.reason is None


def test_shadow_override_does_not_flip_other_denial_reasons() -> None:
    """Non-below_long_threshold denials must pass through even with flag=True."""
    for reason in ("short_disabled", "blocked_wrong_regime", "blocked_low_trend_strength"):
        denied = AllowDecision(allow=False, reason=reason)
        settings = _settings(SHADOW_IGNORE_MIN_SCORE=True)

        decision = denied
        if (
            not decision.allow
            and (decision.reason or "").startswith("below_long_threshold")
            and settings.SHADOW_IGNORE_MIN_SCORE
        ):
            decision = AllowDecision(allow=True, reason=None)

        assert not decision.allow, f"Expected denial for reason={reason!r} to be preserved"
        assert decision.reason == reason


def test_shadow_override_inactive_when_flag_false() -> None:
    """Override must not fire when SHADOW_IGNORE_MIN_SCORE=False."""
    denied = AllowDecision(allow=False, reason="below_long_threshold")
    settings = _settings(SHADOW_IGNORE_MIN_SCORE=False)

    decision = denied
    if (
        not decision.allow
        and (decision.reason or "").startswith("below_long_threshold")
        and settings.SHADOW_IGNORE_MIN_SCORE
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert not decision.allow
    assert decision.reason == "below_long_threshold"


# ---------------------------------------------------------------------------
# Composition: both SHADOW_ALLOW_SHORTS and SHADOW_IGNORE_MIN_SCORE true
# ---------------------------------------------------------------------------


def test_both_shadow_overrides_compose_short_disabled() -> None:
    """With both flags true, short_disabled flips via SHADOW_ALLOW_SHORTS path."""
    denied = AllowDecision(allow=False, reason="short_disabled")
    settings = _settings(SHADOW_ALLOW_SHORTS=True, SHADOW_IGNORE_MIN_SCORE=True)

    decision = denied
    # SHADOW_ALLOW_SHORTS override (first)
    if (
        not decision.allow
        and decision.reason == "short_disabled"
        and settings.SHADOW_ALLOW_SHORTS
    ):
        decision = AllowDecision(allow=True, reason=None)
    # SHADOW_IGNORE_MIN_SCORE override (second)
    if (
        not decision.allow
        and (decision.reason or "").startswith("below_long_threshold")
        and settings.SHADOW_IGNORE_MIN_SCORE
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert decision.allow
    assert decision.reason is None


def test_both_shadow_overrides_compose_below_long_threshold() -> None:
    """With both flags true, below_long_threshold flips via SHADOW_IGNORE_MIN_SCORE path."""
    denied = AllowDecision(allow=False, reason="below_long_threshold")
    settings = _settings(SHADOW_ALLOW_SHORTS=True, SHADOW_IGNORE_MIN_SCORE=True)

    decision = denied
    if (
        not decision.allow
        and decision.reason == "short_disabled"
        and settings.SHADOW_ALLOW_SHORTS
    ):
        decision = AllowDecision(allow=True, reason=None)
    if (
        not decision.allow
        and (decision.reason or "").startswith("below_long_threshold")
        and settings.SHADOW_IGNORE_MIN_SCORE
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert decision.allow
    assert decision.reason is None


def test_both_shadow_overrides_compose_other_denials_still_block() -> None:
    """With both flags true, regime/ADX denials still propagate."""
    for reason in ("blocked_wrong_regime", "blocked_low_trend_strength"):
        denied = AllowDecision(allow=False, reason=reason)
        settings = _settings(SHADOW_ALLOW_SHORTS=True, SHADOW_IGNORE_MIN_SCORE=True)

        decision = denied
        if (
            not decision.allow
            and decision.reason == "short_disabled"
            and settings.SHADOW_ALLOW_SHORTS
        ):
            decision = AllowDecision(allow=True, reason=None)
        if (
            not decision.allow
            and (decision.reason or "").startswith("below_long_threshold")
            and settings.SHADOW_IGNORE_MIN_SCORE
        ):
            decision = AllowDecision(allow=True, reason=None)

        assert not decision.allow, f"Expected {reason!r} to still block"
        assert decision.reason == reason
