"""SP-5 Phase E2 — tier classification with asymmetric SHORT bias.

Per CLAUDE.md rule 9 + MASTER_PLAN §6 line 230: shorts require a +10
percentage-point higher score than longs for each tier (asymmetric risk —
longs go up slowly, shorts go down fast and skip-stops blow up sizing).

LONG thresholds (|final|*100):
    <55%   NO_SIGNAL
    55-65% PAPER
    65-75% SMALL
    75-85% STANDARD
    >=85%  A+

SHORT thresholds (each shifted +10pp):
    <65%   NO_SIGNAL
    65-75% PAPER
    75-85% SMALL
    85-95% STANDARD
    >=95%  A+

NEUTRAL direction always returns NO_SIGNAL regardless of magnitude.
"""
from __future__ import annotations

from typing import Literal

from app.core.scoring.types import Direction, FinalScore

Tier = Literal["NO_SIGNAL", "PAPER", "SMALL", "STANDARD", "A+"]

# Highest threshold first so the linear scan returns the strongest matching tier.
LONG_THRESHOLDS: list[tuple[float, Tier]] = [
    (85.0, "A+"),
    (75.0, "STANDARD"),
    (65.0, "SMALL"),
    (55.0, "PAPER"),
]
SHORT_BIAS_PP: float = 10.0


def classify_tier(final: FinalScore) -> Tier:
    """Map a FinalScore to a tier label.

    NEUTRAL -> always NO_SIGNAL. SHORT direction adds +10pp to every threshold.
    """
    if final.direction is Direction.NEUTRAL:
        return "NO_SIGNAL"
    pct = abs(final.score) * 100.0
    bias = SHORT_BIAS_PP if final.direction is Direction.SHORT else 0.0
    for threshold, tier in LONG_THRESHOLDS:
        if pct >= threshold + bias:
            return tier
    return "NO_SIGNAL"
