from app.core.scoring.types import Direction, FinalScore, LayerScore

_NEUTRAL_BAND = 0.10
_BASE_WEIGHT = 1.0 / 9  # decision 2.3 — equal weights for L1..L9


def aggregate(layer_results: dict[int, LayerScore | None]) -> FinalScore:
    present = {i: s for i, s in layer_results.items() if s is not None and i != 10}
    if not present:
        return FinalScore(
            score=0.0, direction=Direction.NEUTRAL, confidence=0.0,
            layer_results=layer_results, contributing_layers=(),
        )

    # Weight redistribution: each present layer gets _BASE_WEIGHT, then we
    # rescale so the sum of present weights = 1.0 (handles missing layers).
    raw_total_weight = _BASE_WEIGHT * len(present)
    rescale = 1.0 / raw_total_weight if raw_total_weight > 0 else 1.0

    score = 0.0
    confidences: list[float] = []
    for layer in present.values():
        score += _BASE_WEIGHT * rescale * layer.signed_strength * layer.confidence
        confidences.append(layer.confidence)

    score = max(-1.0, min(1.0, score))

    if score > _NEUTRAL_BAND:
        direction = Direction.LONG
    elif score < -_NEUTRAL_BAND:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return FinalScore(
        score=score,
        direction=direction,
        confidence=avg_conf,
        layer_results=layer_results,
        contributing_layers=tuple(sorted(present.keys())),
    )
