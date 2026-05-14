"""SP-5 Phase E2 — tier classification tests."""
from __future__ import annotations

import pytest

from app.core.scoring.tiers import classify_tier
from app.core.scoring.types import Direction, FinalScore


def fs(score: float, direction: Direction) -> FinalScore:
    return FinalScore(
        score=score, direction=direction, confidence=0.5,
        layer_results={}, contributing_layers=(),
    )


@pytest.mark.parametrize("score,expected", [
    (0.20, "NO_SIGNAL"), (0.50, "NO_SIGNAL"),
    (0.55, "PAPER"), (0.60, "PAPER"),
    (0.65, "SMALL"), (0.74, "SMALL"),
    (0.75, "STANDARD"), (0.84, "STANDARD"),
    (0.85, "A+"), (0.95, "A+"),
])
def test_long_thresholds(score: float, expected: str) -> None:
    assert classify_tier(fs(score, Direction.LONG)) == expected


@pytest.mark.parametrize("score,expected", [
    # 2026-05-14: SHORT_BIAS_PP dropped from 10.0 to 0.0 (symmetric flip).
    # SHORT tiers now mirror LONG tiers in magnitude.
    (-0.20, "NO_SIGNAL"), (-0.50, "NO_SIGNAL"),
    (-0.55, "PAPER"), (-0.60, "PAPER"),
    (-0.65, "SMALL"), (-0.74, "SMALL"),
    (-0.75, "STANDARD"), (-0.84, "STANDARD"),
    (-0.85, "A+"), (-0.95, "A+"),
])
def test_short_thresholds_now_symmetric_with_long(score: float, expected: str) -> None:
    assert classify_tier(fs(score, Direction.SHORT)) == expected


def test_neutral_always_no_signal() -> None:
    assert classify_tier(fs(0.99, Direction.NEUTRAL)) == "NO_SIGNAL"
    assert classify_tier(fs(-0.99, Direction.NEUTRAL)) == "NO_SIGNAL"


def test_long_and_short_classify_to_same_tier_at_equal_magnitude() -> None:
    # Direct symmetry check at a few magnitudes.
    for mag in (0.55, 0.65, 0.75, 0.85, 0.95):
        assert classify_tier(fs(mag, Direction.LONG)) == classify_tier(
            fs(-mag, Direction.SHORT)
        )


def test_long_max_score_is_a_plus() -> None:
    assert classify_tier(fs(1.0, Direction.LONG)) == "A+"
