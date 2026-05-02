import pytest
from app.core.scoring.types import LayerScore, FinalScore, Direction


def test_layer_score_has_required_fields() -> None:
    s = LayerScore(direction=Direction.LONG, strength=0.7, confidence=0.8, notes="rsi above 50")
    assert s.direction is Direction.LONG
    assert s.strength == 0.7
    assert s.confidence == 0.8


def test_strength_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError):
        LayerScore(direction=Direction.LONG, strength=1.5, confidence=0.8)


def test_signed_strength_is_negative_for_short() -> None:
    s = LayerScore(direction=Direction.SHORT, strength=0.6, confidence=0.9)
    assert s.signed_strength == pytest.approx(-0.6)


def test_signed_strength_zero_for_neutral() -> None:
    s = LayerScore(direction=Direction.NEUTRAL, strength=0.0, confidence=1.0)
    assert s.signed_strength == 0.0


def test_final_score_carries_layer_results() -> None:
    fs = FinalScore(
        score=0.42, direction=Direction.LONG, confidence=0.7,
        layer_results={1: None, 3: None, 5: None},
        contributing_layers=(1, 3),
    )
    assert fs.score == 0.42
    assert 1 in fs.contributing_layers
