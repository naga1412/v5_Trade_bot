import logging

import pandas as pd
import pytest

from app.core.patterns import base as patterns_base
from app.core.patterns.base import Pattern, PatternFire, detect_safe


def test_pattern_fire_minimal_construction() -> None:
    fire = PatternFire(
        pattern_id="hammer",
        direction="LONG",
        strength=0.7,
        confidence=0.8,
        evidence={"hammer_ratio": 2.3},
    )
    assert fire.pattern_id == "hammer"
    assert fire.direction == "LONG"
    assert fire.strength == pytest.approx(0.7)
    assert fire.confidence == pytest.approx(0.8)
    assert fire.evidence == {"hammer_ratio": 2.3}


def test_pattern_fire_strength_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="strength must be in"):
        PatternFire(pattern_id="x", direction="LONG", strength=1.5,
                    confidence=0.5, evidence={})


def test_pattern_fire_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        PatternFire(pattern_id="x", direction="LONG", strength=0.5,
                    confidence=-0.1, evidence={})


def test_pattern_fire_invalid_direction_rejected() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        PatternFire(pattern_id="x", direction="SIDEWAYS",  # type: ignore[arg-type]
                    strength=0.5, confidence=0.5, evidence={})


def test_pattern_fire_is_frozen() -> None:
    fire = PatternFire(pattern_id="x", direction="LONG",
                       strength=0.5, confidence=0.5, evidence={})
    with pytest.raises(Exception):  # FrozenInstanceError
        fire.strength = 0.9  # type: ignore[misc]


def test_pattern_protocol_shape() -> None:
    """Pattern Protocol declares attrs + detect method. A class implementing
    the protocol satisfies isinstance(x, Pattern) at runtime via @runtime_checkable."""
    class FakePattern:
        pattern_id = "fake"
        pattern_type = "candle"

        def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
            return None

    fp = FakePattern()
    assert isinstance(fp, Pattern)


# ---------------------------------------------------------------------------
# 2026-08-14 remediation work order B2: detect_safe -- was a bare
# `except Exception: continue` in layer2_patterns.py and layer6_micro.py,
# zero logging, zero per-pattern failure tracking. Same failure class as
# the flow_features endpoint-swallow fixed in PR #423.
# ---------------------------------------------------------------------------


class _RaisingPattern:
    pattern_id = "broken_pattern"
    pattern_type = "candle"

    def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
        raise RuntimeError("boom")


class _WorkingPattern:
    pattern_id = "fine_pattern"
    pattern_type = "candle"

    def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
        return None


@pytest.fixture(autouse=True)
def _reset_pattern_failure_streaks():
    patterns_base._clear_pattern_failure_streaks_for_tests()
    yield
    patterns_base._clear_pattern_failure_streaks_for_tests()


def test_detect_safe_returns_none_on_raise_without_bricking(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.patterns.base")
    result = detect_safe(_RaisingPattern(), pd.DataFrame(), 0)
    assert result is None
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_detect_safe_isolated_single_failure_does_not_escalate(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.patterns.base")
    for _ in range(5):
        detect_safe(_RaisingPattern(), pd.DataFrame(), 0)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_detect_safe_systematic_failure_escalates_to_error(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.patterns.base")
    for _ in range(patterns_base._CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
        detect_safe(_RaisingPattern(), pd.DataFrame(), 0)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected an ERROR-level escalation once the streak hit threshold"
    assert "broken_pattern" in error_records[-1].getMessage()
    assert "consecutive" in error_records[-1].getMessage().lower()


def test_detect_safe_success_resets_the_failure_streak(caplog) -> None:
    """A single success between failure runs must reset the streak, so two
    partial runs (e.g. across two candles) don't sum into a false escalation."""
    caplog.set_level(logging.DEBUG, logger="app.core.patterns.base")
    threshold = patterns_base._CONSECUTIVE_FAILURE_ALERT_THRESHOLD

    class _FlakyPattern:
        pattern_id = "flaky_pattern"
        pattern_type = "candle"
        should_raise = True

        def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
            if self.should_raise:
                raise RuntimeError("boom")
            return None

    flaky = _FlakyPattern()
    for _ in range(threshold - 1):
        detect_safe(flaky, pd.DataFrame(), 0)
    flaky.should_raise = False
    detect_safe(flaky, pd.DataFrame(), 0)  # success resets this pattern's streak
    flaky.should_raise = True
    for _ in range(threshold - 1):
        detect_safe(flaky, pd.DataFrame(), 0)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_detect_safe_each_pattern_tracked_independently(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.patterns.base")
    threshold = patterns_base._CONSECUTIVE_FAILURE_ALERT_THRESHOLD
    for _ in range(threshold - 1):
        detect_safe(_RaisingPattern(), pd.DataFrame(), 0)

    class _OtherRaisingPattern:
        pattern_id = "other_broken_pattern"
        pattern_type = "candle"

        def detect(self, bars: pd.DataFrame, current_idx: int) -> PatternFire | None:
            raise RuntimeError("boom")

    detect_safe(_OtherRaisingPattern(), pd.DataFrame(), 0)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
