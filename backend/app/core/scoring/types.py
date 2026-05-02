from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class LayerScore:
    direction: Direction
    strength: float           # [0, 1] — magnitude of conviction
    confidence: float         # [0, 1] — meta-confidence in the score itself
    notes: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")

    @property
    def signed_strength(self) -> float:
        if self.direction is Direction.LONG:
            return self.strength
        if self.direction is Direction.SHORT:
            return -self.strength
        return 0.0


@dataclass(frozen=True)
class FinalScore:
    score: float                                   # signed, [-1, +1]
    direction: Direction
    confidence: float                              # [0, 1]
    layer_results: dict[int, "LayerScore | None"]  # 1..10 mapped (some None)
    contributing_layers: tuple[int, ...] = field(default_factory=tuple)
