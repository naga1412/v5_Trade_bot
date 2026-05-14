"""SP-5 — full FINAL_SCORE aggregator.

Extends the SP-0 baseline aggregator with the MASTER_PLAN §5 formula:

    static = sum_active(weight * signed_strength * confidence)   # weight=1/N_active
    raw_final = static * brain_adjust * (1-0.15)^min(trap_count,4) * news_multiplier
    direction = LONG if raw_final > 0.05 else SHORT if raw_final < -0.05 else NEUTRAL
    final = raw_final * (1.0 if LONG else 0.95 if SHORT else 1.0)

Layer 10 is excluded from the static average (per existing convention) — SP-4
will instead supply BRAIN_ADJUST.

Backward compatibility: callers passing only ``layer_results`` still work; the
new keyword-only ``trap_fires`` / ``brain_adjust`` / ``news_multiplier`` default
to neutral values. The SHORT direction penalty (0.95) does change output for
pure-SHORT scores — see CLAUDE.md rule 9 + MASTER_PLAN §5 line 215.
"""
from __future__ import annotations

from app.core.scoring.traps.base import TrapFire
from app.core.scoring.types import Direction, FinalScore, LayerScore

_NEUTRAL_BAND: float = 0.05
_BASE_WEIGHT: float = 1.0 / 9
_TRAP_PENALTY: float = 0.15
_TRAP_CAP: int = 4
_BRAIN_ADJUST_MIN: float = 0.0
_BRAIN_ADJUST_MAX: float = 2.0
_SHORT_DIRECTION_PENALTY: float = 0.95


def aggregate(
    layer_results: dict[int, LayerScore | None],
    *,
    trap_fires: list[TrapFire] | None = None,
    brain_adjust: float = 1.0,
    news_multiplier: float = 1.0,
    direction_calibration: dict[str, float] | None = None,
) -> FinalScore:
    """Apply the full SP-5 FINAL_SCORE formula.

    SP-0 callers (no kwargs) get the same static computation as before plus
    the SHORT direction penalty (which is a behaviour change for pure-SHORT
    layer mixes).

    Raises:
        ValueError: ``brain_adjust`` outside the open interval (0.0, 2.0).
    """
    if not (_BRAIN_ADJUST_MIN < brain_adjust < _BRAIN_ADJUST_MAX):
        raise ValueError(
            f"brain_adjust must be in ({_BRAIN_ADJUST_MIN}, {_BRAIN_ADJUST_MAX}), "
            f"got {brain_adjust}"
        )

    present = {i: s for i, s in layer_results.items() if s is not None and i != 10}
    if not present:
        return FinalScore(
            score=0.0, direction=Direction.NEUTRAL, confidence=0.0,
            layer_results=layer_results, contributing_layers=(),
        )

    raw_total_weight = _BASE_WEIGHT * len(present)
    rescale = 1.0 / raw_total_weight if raw_total_weight > 0 else 1.0
    static = 0.0
    confidences: list[float] = []
    for layer in present.values():
        static += _BASE_WEIGHT * rescale * layer.signed_strength * layer.confidence
        confidences.append(layer.confidence)
    static = max(-1.0, min(1.0, static))

    fires = trap_fires or []
    effective_count = min(len(fires), _TRAP_CAP)
    trap_factor = (1.0 - _TRAP_PENALTY) ** effective_count

    raw_final = static * brain_adjust * trap_factor * news_multiplier

    if raw_final > _NEUTRAL_BAND:
        direction = Direction.LONG
    elif raw_final < -_NEUTRAL_BAND:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL

    direction_penalty = _SHORT_DIRECTION_PENALTY if direction is Direction.SHORT else 1.0
    # Per-direction online calibration (best-effort): when supplied,
    # multiplies the raw_final by an empirical multiplier learned from
    # prediction_validations realized outcomes. See
    # app/core/scoring/calibration.py — the multiplier is capped to
    # [0.7, 1.3] so it cannot run away. When direction_calibration is
    # None (legacy path), this is a no-op.
    calibration_multiplier = 1.0
    if direction_calibration is not None:
        calibration_multiplier = direction_calibration.get(direction.value, 1.0)
    final = raw_final * direction_penalty * calibration_multiplier
    final = max(-1.0, min(1.0, final))

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return FinalScore(
        score=final,
        direction=direction,
        confidence=avg_conf,
        layer_results=layer_results,
        contributing_layers=tuple(sorted(present.keys())),
    )
