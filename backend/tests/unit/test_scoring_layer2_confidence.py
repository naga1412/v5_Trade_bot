"""Tests for the L2 layer confidence formula fix (Phase 2-A).

Root problem: old formula `min(1.0, len(fires) / 10.0)` gave confidence=0.1
for a single chart pattern — making textbook reversals nearly invisible in the
aggregator. The fix distinguishes chart patterns (high-signal from 1 fire) from
candle patterns (need plurality to be reliable).
"""
from __future__ import annotations

import pytest

from app.core.patterns.base import PatternFire
from app.core.scoring.layer2_patterns import _CHART_PATTERN_IDS, _compute_layer_confidence


# Pick a real chart and candle pattern ID from the registry.
_CHART_ID = "double_bottom"       # registered in app.core.patterns.chart
_CANDLE_ID = "abandoned_baby"     # registered in app.core.patterns.candle


def _chart_fire(strength: float = 0.85, confidence: float = 0.85) -> PatternFire:
    return PatternFire(
        pattern_id=_CHART_ID, direction="LONG",
        strength=strength, confidence=confidence, evidence={},
    )


def _candle_fire(strength: float = 0.60, confidence: float = 0.60) -> PatternFire:
    return PatternFire(
        pattern_id=_CANDLE_ID, direction="LONG",
        strength=strength, confidence=confidence, evidence={},
    )


# --- registry sanity ---

def test_chart_id_is_in_chart_pattern_ids() -> None:
    assert _CHART_ID in _CHART_PATTERN_IDS


def test_candle_id_is_not_in_chart_pattern_ids() -> None:
    assert _CANDLE_ID not in _CHART_PATTERN_IDS


# --- no fires ---

def test_no_fires_returns_zero() -> None:
    assert _compute_layer_confidence([]) == 0.0


# --- chart pattern confidence ---

def test_one_chart_fire_high_quality_gives_meaningful_confidence() -> None:
    # Old formula: 1/10 = 0.10. New: avg_quality = 0.85*0.85 = 0.7225 → ~0.72
    result = _compute_layer_confidence([_chart_fire(0.85, 0.85)])
    assert result >= 0.60, f"expected ≥0.60, got {result}"


def test_one_chart_fire_gives_significantly_more_than_old_formula() -> None:
    result = _compute_layer_confidence([_chart_fire(0.85, 0.85)])
    old_formula = min(1.0, 1 / 10.0)  # 0.10
    assert result > old_formula * 4, (
        f"new formula ({result:.3f}) should be >4× old formula ({old_formula})"
    )


def test_two_chart_fires_higher_than_one() -> None:
    one = _compute_layer_confidence([_chart_fire()])
    two = _compute_layer_confidence([_chart_fire(), _chart_fire()])
    assert two > one


def test_three_chart_fires_saturates_near_0_95() -> None:
    result = _compute_layer_confidence([_chart_fire(), _chart_fire(), _chart_fire()])
    assert result >= 0.80


def test_weak_chart_fire_gives_lower_confidence() -> None:
    strong = _compute_layer_confidence([_chart_fire(0.85, 0.85)])
    weak = _compute_layer_confidence([_chart_fire(0.30, 0.30)])
    assert weak < strong


def test_chart_confidence_capped_at_0_95() -> None:
    fires = [_chart_fire(1.0, 1.0)] * 10
    result = _compute_layer_confidence(fires)
    assert result <= 0.95


# --- candle pattern confidence ---

def test_one_candle_fire_gives_low_confidence() -> None:
    result = _compute_layer_confidence([_candle_fire()])
    assert result <= 0.15, f"1 candle fire should give low confidence, got {result}"


def test_five_candle_fires_gives_moderate_confidence() -> None:
    result = _compute_layer_confidence([_candle_fire()] * 5)
    assert 0.45 <= result <= 0.55, f"5 candle fires → ~0.5, got {result}"


def test_ten_candle_fires_gives_0_80_max() -> None:
    result = _compute_layer_confidence([_candle_fire()] * 10)
    assert result == pytest.approx(0.80, abs=0.01)


def test_many_candle_fires_capped_at_0_80() -> None:
    result = _compute_layer_confidence([_candle_fire()] * 20)
    assert result <= 0.80


# --- mixed chart + candle ---

def test_chart_dominates_mixed_fires() -> None:
    # One strong chart fire (conf ~0.72) + 1 candle fire (conf 0.10).
    result = _compute_layer_confidence([_chart_fire(), _candle_fire()])
    chart_only = _compute_layer_confidence([_chart_fire()])
    # Mixed result should equal or exceed chart-only (chart dominates via max).
    assert result >= chart_only * 0.95
