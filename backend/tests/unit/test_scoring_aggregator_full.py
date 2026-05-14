"""SP-5 Phase E1 — extended FINAL_SCORE formula tests."""
from __future__ import annotations

import pytest

from app.core.scoring.aggregator import aggregate
from app.core.scoring.traps.base import TrapFire
from app.core.scoring.types import Direction, LayerScore


def L(direction: Direction, strength: float, confidence: float = 0.8) -> LayerScore:
    return LayerScore(direction, strength, confidence)


def F(trap_id: str, side: str = "both") -> TrapFire:
    return TrapFire(trap_id=trap_id, severity="high", side=side, reason="t", evidence={})


def test_no_traps_no_brain_no_news_matches_legacy() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.8)
    scores[3] = L(Direction.LONG, 0.8)
    fs = aggregate(scores)
    assert fs.score > 0.0
    assert fs.direction is Direction.LONG


def test_trap_factor_compounds_per_trap() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 1.0, 1.0)
    fs0 = aggregate(scores, trap_fires=[])
    fs1 = aggregate(scores, trap_fires=[F("t1")])
    fs2 = aggregate(scores, trap_fires=[F("t1"), F("t2")])
    assert fs1.score == pytest.approx(fs0.score * 0.85, abs=1e-6)
    assert fs2.score == pytest.approx(fs0.score * 0.85 * 0.85, abs=1e-6)


def test_trap_count_capped_at_4() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 1.0, 1.0)
    fs5 = aggregate(scores, trap_fires=[F(f"t{i}") for i in range(5)])
    fs4 = aggregate(scores, trap_fires=[F(f"t{i}") for i in range(4)])
    assert fs5.score == pytest.approx(fs4.score, abs=1e-6)


def test_brain_adjust_multiplies() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.5, 1.0)
    base = aggregate(scores).score
    boosted = aggregate(scores, brain_adjust=1.2).score
    assert boosted == pytest.approx(base * 1.2, abs=1e-6)


def test_brain_adjust_out_of_range_raises() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.5, 1.0)
    with pytest.raises(ValueError):
        aggregate(scores, brain_adjust=-0.5)
    with pytest.raises(ValueError):
        aggregate(scores, brain_adjust=2.5)


def test_news_multiplier_multiplies() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 0.5, 1.0)
    base = aggregate(scores).score
    boosted = aggregate(scores, news_multiplier=0.8).score
    assert boosted == pytest.approx(base * 0.8, abs=1e-6)


def test_short_direction_penalty_neutralised_to_1p0() -> None:
    # 2026-05-14: the 0.95 SHORT penalty was removed (operator decision to
    # trade both sides symmetrically). SHORT now produces the same raw
    # magnitude as the equivalent LONG.
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.SHORT, 1.0, 1.0)
    fs = aggregate(scores)
    assert fs.score == pytest.approx(-1.0, abs=1e-6)


def test_long_direction_penalty_1p0() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[1] = L(Direction.LONG, 1.0, 1.0)
    fs = aggregate(scores)
    assert fs.score == pytest.approx(1.0, abs=1e-6)


def test_short_and_long_now_symmetric_in_magnitude() -> None:
    """Equivalent strengths in opposite directions produce equal |scores|."""
    long_scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    short_scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    long_scores[1] = L(Direction.LONG, 0.7, 0.9)
    short_scores[1] = L(Direction.SHORT, 0.7, 0.9)
    fs_l = aggregate(long_scores)
    fs_s = aggregate(short_scores)
    assert fs_l.score == pytest.approx(-fs_s.score, abs=1e-9)
