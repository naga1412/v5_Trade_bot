"""Tests for SHADOW_ALLOW_SHORTS config flag.

Verifies:
1. open_position_gate (the LIVE dispatch path) still denies SHORT even when
   SHADOW_ALLOW_SHORTS=True — the flag must live ONLY in the shadow worker
   override, never inside the gate itself.
2. SHADOW_ALLOW_SHORTS defaults to False (no change to current behaviour).
3. LONG decisions are unaffected by the flag.
4. Shadow worker's override: a short_disabled denial flips to allow when the
   flag is set — all other denial reasons still propagate.
"""
from __future__ import annotations

import dataclasses

from app.config import Settings
from app.core.gates.entry_quality import AllowDecision, open_position_gate


@dataclasses.dataclass
class _Sig:
    """Minimal duck-type matching the two attrs open_position_gate reads."""
    direction: str
    entry_score: float = -0.6


def _settings(**kwargs) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql://x",
        redis_url="redis://x",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Gate-level tests (live dispatch path — must NOT be affected by the flag)
# ---------------------------------------------------------------------------


def test_live_gate_denies_short_regardless_of_shadow_flag() -> None:
    """open_position_gate must deny SHORT even when SHADOW_ALLOW_SHORTS=True.

    The live dispatch path calls open_position_gate directly; it must be
    completely unaffected by SHADOW_ALLOW_SHORTS.
    """
    settings = _settings(DISABLE_SHORT_SIGNALS=True, SHADOW_ALLOW_SHORTS=True)
    decision = open_position_gate(_Sig(direction="SHORT"), settings)
    assert not decision.allow
    assert decision.reason == "short_disabled"


def test_live_gate_allows_long_unaffected_by_shadow_flag() -> None:
    """SHADOW_ALLOW_SHORTS must not affect LONG decisions."""
    settings = _settings(DISABLE_SHORT_SIGNALS=True, SHADOW_ALLOW_SHORTS=True)
    decision = open_position_gate(_Sig(direction="LONG", entry_score=0.4), settings)
    assert decision.allow


def test_live_gate_denies_short_without_shadow_flag() -> None:
    """Baseline: gate still denies SHORT when SHADOW_ALLOW_SHORTS=False."""
    settings = _settings(DISABLE_SHORT_SIGNALS=True, SHADOW_ALLOW_SHORTS=False)
    decision = open_position_gate(_Sig(direction="SHORT"), settings)
    assert not decision.allow
    assert decision.reason == "short_disabled"


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_shadow_allow_shorts_default_false() -> None:
    """SHADOW_ALLOW_SHORTS must default to False — no behaviour change on deploy."""
    s = _settings()
    assert s.SHADOW_ALLOW_SHORTS is False


def test_disable_short_signals_default_false() -> None:
    """DISABLE_SHORT_SIGNALS must still default to False (no regression)."""
    s = _settings()
    assert s.DISABLE_SHORT_SIGNALS is False


# ---------------------------------------------------------------------------
# Shadow-worker override logic (unit-level, exercising the AllowDecision path)
# ---------------------------------------------------------------------------


def test_shadow_override_flips_short_disabled_to_allow() -> None:
    """When SHADOW_ALLOW_SHORTS=True, a short_disabled denial becomes allow."""
    denied = AllowDecision(allow=False, reason="short_disabled")
    settings = _settings(SHADOW_ALLOW_SHORTS=True)

    # Simulate the shadow worker's override block
    decision = denied
    if (
        not decision.allow
        and decision.reason == "short_disabled"
        and settings.SHADOW_ALLOW_SHORTS
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert decision.allow
    assert decision.reason is None


def test_shadow_override_does_not_flip_other_denial_reasons() -> None:
    """Non-short_disabled denials must pass through even with SHADOW_ALLOW_SHORTS=True."""
    for reason in ("below_long_threshold", "blocked_wrong_regime", "blocked_low_trend_strength"):
        denied = AllowDecision(allow=False, reason=reason)
        settings = _settings(SHADOW_ALLOW_SHORTS=True)

        decision = denied
        if (
            not decision.allow
            and decision.reason == "short_disabled"
            and settings.SHADOW_ALLOW_SHORTS
        ):
            decision = AllowDecision(allow=True, reason=None)

        assert not decision.allow, f"Expected denial for reason={reason!r} to be preserved"
        assert decision.reason == reason


def test_shadow_override_inactive_when_flag_false() -> None:
    """Override must not fire when SHADOW_ALLOW_SHORTS=False (flag off)."""
    denied = AllowDecision(allow=False, reason="short_disabled")
    settings = _settings(SHADOW_ALLOW_SHORTS=False)

    decision = denied
    if (
        not decision.allow
        and decision.reason == "short_disabled"
        and settings.SHADOW_ALLOW_SHORTS
    ):
        decision = AllowDecision(allow=True, reason=None)

    assert not decision.allow
    assert decision.reason == "short_disabled"
