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
    (-0.50, "NO_SIGNAL"), (-0.60, "NO_SIGNAL"),
    (-0.65, "PAPER"), (-0.74, "PAPER"),
    (-0.75, "SMALL"), (-0.84, "SMALL"),
    (-0.85, "STANDARD"), (-0.94, "STANDARD"),
    (-0.95, "A+"),
])
def test_short_thresholds_have_10pp_higher_bar(score: float, expected: str) -> None:
    assert classify_tier(fs(score, Direction.SHORT)) == expected


def test_neutral_always_no_signal() -> None:
    assert classify_tier(fs(0.99, Direction.NEUTRAL)) == "NO_SIGNAL"
    assert classify_tier(fs(-0.99, Direction.NEUTRAL)) == "NO_SIGNAL"


def test_short_just_below_a_plus_is_standard() -> None:
    # -0.94 abs is 94% < 95% bias-adjusted A+ threshold -> STANDARD
    assert classify_tier(fs(-0.949, Direction.SHORT)) == "STANDARD"


def test_long_max_score_is_a_plus() -> None:
    assert classify_tier(fs(1.0, Direction.LONG)) == "A+"
