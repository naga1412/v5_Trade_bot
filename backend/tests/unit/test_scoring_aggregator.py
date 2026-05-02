import pytest

from app.core.scoring.aggregator import aggregate
from app.core.scoring.types import LayerScore, Direction


def L(direction: Direction, strength: float, confidence: float = 0.8) -> LayerScore:
    return LayerScore(direction, strength, confidence)


def test_all_long_layers_aggregate_to_long() -> None:
    scores = {i: L(Direction.LONG, 0.8) for i in (1, 3, 5)}
    scores.update({i: None for i in (2, 4, 6, 7, 8, 9, 10)})
    fs = aggregate(scores)
    assert fs.direction is Direction.LONG
    assert fs.score > 0.10
    assert fs.contributing_layers == (1, 3, 5)


def test_mixed_layers_can_neutralise() -> None:
    scores = {
        1: L(Direction.LONG, 0.8),
        3: L(Direction.SHORT, 0.8),
        5: L(Direction.NEUTRAL, 0.0),
    }
    scores.update({i: None for i in (2, 4, 6, 7, 8, 9, 10)})
    fs = aggregate(scores)
    assert abs(fs.score) <= 0.10
    assert fs.direction is Direction.NEUTRAL


def test_single_layer_can_drive_direction() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[3] = L(Direction.SHORT, 1.0, confidence=1.0)
    fs = aggregate(scores)
    # Only L3 present; weight redistributes to 1.0
    assert fs.direction is Direction.SHORT
    assert fs.score == pytest.approx(-1.0)


def test_no_layers_present_returns_neutral_zero() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    fs = aggregate(scores)
    assert fs.score == 0.0
    assert fs.direction is Direction.NEUTRAL
    assert fs.confidence == 0.0


def test_score_clamped_to_unit_interval() -> None:
    scores = {i: L(Direction.LONG, 1.0, confidence=1.0) for i in range(1, 10)}
    scores[10] = None
    fs = aggregate(scores)
    assert fs.score == pytest.approx(1.0)
