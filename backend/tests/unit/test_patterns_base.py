import pandas as pd
import pytest

from app.core.patterns.base import Pattern, PatternFire


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
