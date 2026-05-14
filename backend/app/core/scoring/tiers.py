"""SP-5 Phase E2 — tier classification (symmetric LONG/SHORT as of 2026-05-14).

Original design (CLAUDE.md rule 9 + MASTER_PLAN §6 line 230) added a +10pp
SHORT bias on every tier because crypto squeezes can blow through short
stops. Operator decision: trade both sides on equal terms so the validation
ledger actually accumulates SHORT data on the same magnitude bar.
The constant ``SHORT_BIAS_PP`` is kept (and set to 0.0) rather than deleted
so that re-enabling asymmetry later is a one-line change.

Thresholds (|final|*100), LONG and SHORT both:
    <55%   NO_SIGNAL
    55-65% PAPER
    65-75% SMALL
    75-85% STANDARD
    >=85%  A+

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
SHORT_BIAS_PP: float = 0.0


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
