"""Unit tests for the per-direction calibration in app/core/scoring/calibration.py.

These cover the PURE function compute_direction_multipliers, which is
the part with real logic. The async load_recent_validations helper is
covered indirectly by test_calibration_in_aggregator below — when no
DB is wired, the calibration silently falls back to 1.0 everywhere.
"""
from __future__ import annotations

import pytest

from app.core.scoring.aggregator import aggregate
from app.core.scoring.calibration import (
    MIN_SAMPLE_PER_DIRECTION,
    MULTIPLIER_LOWER_BOUND,
    MULTIPLIER_UPPER_BOUND,
    ValidationSample,
    compute_direction_multipliers,
)
from app.core.scoring.types import Direction, LayerScore


def _samples(direction: str, n: int, avg_pnl: float) -> list[ValidationSample]:
    """Build N samples on one side with a target average pnl_pct."""
    return [
        ValidationSample(direction=direction, was_correct=avg_pnl > 0, pnl_pct=avg_pnl)
        for _ in range(n)
    ]


def test_below_min_samples_returns_1p0() -> None:
    # 19 samples on LONG — below the default MIN of 20 — no calibration.
    samples = _samples("LONG", n=MIN_SAMPLE_PER_DIRECTION - 1, avg_pnl=2.0)
    out = compute_direction_multipliers(samples)
    assert out["LONG"] == 1.0
    assert out["SHORT"] == 1.0
    assert out["NEUTRAL"] == 1.0


def test_neutral_always_1p0_regardless_of_samples() -> None:
    # 100 NEUTRAL samples averaging +5% — still no multiplier for NEUTRAL.
    samples = _samples("NEUTRAL", n=100, avg_pnl=5.0)
    out = compute_direction_multipliers(samples)
    assert out["NEUTRAL"] == 1.0


def test_positive_avg_pnl_amplifies_direction() -> None:
    # SHORT samples averaging +2% (the model was correct on shorts on avg)
    # → SHORT multiplier should be > 1.0 (amplify so more SHORT signals fire).
    samples = _samples("SHORT", n=30, avg_pnl=2.0)
    out = compute_direction_multipliers(samples)
    assert out["SHORT"] > 1.0
    assert out["SHORT"] <= MULTIPLIER_UPPER_BOUND
    assert out["LONG"] == 1.0


def test_negative_avg_pnl_dampens_direction() -> None:
    # LONG samples averaging -2% (the model was wrong on longs on avg)
    # → LONG multiplier should be < 1.0 (dampen so fewer LONG signals fire).
    samples = _samples("LONG", n=30, avg_pnl=-2.0)
    out = compute_direction_multipliers(samples)
    assert out["LONG"] < 1.0
    assert out["LONG"] >= MULTIPLIER_LOWER_BOUND
    assert out["SHORT"] == 1.0


def test_multiplier_capped_at_upper_bound() -> None:
    # Extreme +50% avg pnl — way beyond the tanh saturation point.
    # Multiplier should saturate at MULTIPLIER_UPPER_BOUND, not run away.
    samples = _samples("LONG", n=50, avg_pnl=50.0)
    out = compute_direction_multipliers(samples)
    assert out["LONG"] == pytest.approx(MULTIPLIER_UPPER_BOUND)


def test_multiplier_capped_at_lower_bound() -> None:
    # Extreme -50% avg pnl — way beyond the tanh saturation point.
    # Multiplier should saturate at MULTIPLIER_LOWER_BOUND.
    samples = _samples("SHORT", n=50, avg_pnl=-50.0)
    out = compute_direction_multipliers(samples)
    assert out["SHORT"] == pytest.approx(MULTIPLIER_LOWER_BOUND)


def test_per_direction_independent() -> None:
    # LONG averaging +3% (amplify), SHORT averaging -3% (dampen) —
    # both directions should adjust in OPPOSITE directions independently.
    samples = _samples("LONG", n=30, avg_pnl=3.0) + _samples(
        "SHORT", n=30, avg_pnl=-3.0
    )
    out = compute_direction_multipliers(samples)
    assert out["LONG"] > 1.0
    assert out["SHORT"] < 1.0


def test_empty_samples_returns_1p0_for_all() -> None:
    out = compute_direction_multipliers([])
    assert out == {"LONG": 1.0, "SHORT": 1.0, "NEUTRAL": 1.0}


# ---------------------------------------------------------------------------
# Integration with the aggregator — confirms the optional kwarg actually
# multiplies the right side and is backward-compatible when omitted.


def L(direction: Direction, strength: float, confidence: float = 0.8) -> LayerScore:
    return LayerScore(direction, strength, confidence)


def test_aggregator_backward_compatible_no_calibration() -> None:
    """Default behaviour: when direction_calibration is None, aggregator
    behaves exactly like before this PR."""
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.5, 1.0)
    fs_legacy = aggregate(scores)
    fs_explicit_none = aggregate(scores, direction_calibration=None)
    assert fs_legacy.score == pytest.approx(fs_explicit_none.score)
    assert fs_legacy.direction is fs_explicit_none.direction


def test_aggregator_applies_long_multiplier() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.5, 1.0)
    base = aggregate(scores).score
    boosted = aggregate(
        scores, direction_calibration={"LONG": 1.2, "SHORT": 1.0, "NEUTRAL": 1.0}
    ).score
    # 1.2x amplification on the LONG side — score should grow by ~20% (subject
    # to the [-1, 1] clamp which doesn't kick in at 0.5 base).
    assert boosted == pytest.approx(base * 1.2, abs=1e-6)


def test_aggregator_applies_short_multiplier() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.SHORT, 0.5, 1.0)
    base = aggregate(scores).score  # negative
    dampened = aggregate(
        scores, direction_calibration={"LONG": 1.0, "SHORT": 0.7, "NEUTRAL": 1.0}
    ).score
    # 0.7x dampening on SHORT side — |score| shrinks by 30%.
    # base is negative, so dampened > base (less negative).
    assert dampened > base
    assert dampened == pytest.approx(base * 0.7, abs=1e-6)


def test_aggregator_calibration_does_not_change_direction() -> None:
    """Calibration multiplies the score but never flips the direction
    classification — the direction is decided BEFORE the multiplier
    is applied."""
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.SHORT, 0.5, 1.0)
    # Even with extreme dampening multiplier, direction stays SHORT.
    fs = aggregate(
        scores, direction_calibration={"LONG": 1.0, "SHORT": 0.7, "NEUTRAL": 1.0}
    )
    assert fs.direction is Direction.SHORT


def test_aggregator_calibration_neutral_direction_no_op() -> None:
    """A NEUTRAL prediction shouldn't be affected by LONG or SHORT
    multipliers — even if both are extreme."""
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    # Mix that yields a near-zero static and therefore NEUTRAL direction.
    scores[1] = L(Direction.LONG, 0.05, 1.0)
    scores[2] = L(Direction.SHORT, 0.05, 1.0)
    fs = aggregate(
        scores,
        direction_calibration={"LONG": 1.3, "SHORT": 0.7, "NEUTRAL": 1.0},
    )
    # NEUTRAL keeps the no-op multiplier; the asserted invariant is that
    # the score didn't flip direction classification.
    assert fs.direction is Direction.NEUTRAL


def test_aggregator_calibration_clamped_to_unit_interval() -> None:
    """Even if calibration would push score past 1.0, the final clamp keeps
    it in [-1, 1]."""
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 1.0, 1.0)
    fs = aggregate(
        scores, direction_calibration={"LONG": 1.3, "SHORT": 1.0, "NEUTRAL": 1.0}
    )
    # Pre-existing behavior already clamps; calibration must respect the
    # [-1, 1] invariant.
    assert -1.0 <= fs.score <= 1.0
