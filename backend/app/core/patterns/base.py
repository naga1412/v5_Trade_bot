"""Pattern detection primitives — spec §3.2.

`PatternFire` is the value object every pattern returns when detected.
`Pattern` is a Protocol describing the detector interface (no inheritance
required; any class with the matching attrs/method satisfies it).
"""
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

Direction = Literal["LONG", "SHORT"]
PatternType = Literal["candle", "chart"]
_VALID_DIRECTIONS: frozenset[str] = frozenset({"LONG", "SHORT"})


@dataclass(frozen=True)
class PatternFire:
    """A single pattern detection at one bar.

    Attributes:
        pattern_id: stable string id (snake_case) used as the lookup key in
            `pattern_stats` and `pattern_enabled`.
        direction: "LONG" or "SHORT" — the bias the pattern implies.
        strength: how strongly the pattern is formed, in [0, 1].
        confidence: how clean / unambiguous the formation is, in [0, 1].
            Distinct from strength: a faint-but-clean pattern has low
            strength + high confidence; a strong-but-noisy pattern has the
            opposite.
        evidence: free-form dict for diagnostics (e.g. wick ratios, lookback,
            peak indices). Persisted into `predictions.layer_scores["2"].notes`
            up to a 500-char limit (see Phase E task E3).
    """
    pattern_id: str
    direction: Direction
    strength: float
    confidence: float
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}, "
                f"got {self.direction!r}"
            )
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")


@runtime_checkable
class Pattern(Protocol):
    """Detector protocol — every candle and chart pattern implements this."""
    pattern_id: str
    pattern_type: PatternType

    def detect(
        self, bars: pd.DataFrame, current_idx: int
    ) -> PatternFire | None:
        """Run detection on `bars` ending at `current_idx`.

        Args:
            bars: DataFrame with columns `open`, `high`, `low`, `close`,
                `volume`, indexed by `pd.DatetimeIndex` ascending.
            current_idx: positional index (0-based) of the bar to evaluate.
                Patterns should look only at `bars.iloc[:current_idx + 1]`.

        Returns:
            `PatternFire` if the pattern is detected at `current_idx`,
            otherwise `None`. Must NOT raise on bad input — return `None`.
        """
        ...
