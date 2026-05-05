"""Tests for harmonic_crab chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.harmonic_crab import HarmonicCrabPattern
from tests.unit._chart_test_utils import flat_bars, random_walk


def test_hc_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = HarmonicCrabPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_hc_id_and_type() -> None:
    p = HarmonicCrabPattern()
    assert p.pattern_id == "harmonic_crab"
    assert p.pattern_type == "chart"


def test_hc_returns_valid_or_none_on_random() -> None:
    bars = random_walk(120, seed=13)
    fire = HarmonicCrabPattern().detect(bars, current_idx=len(bars) - 1)
    if fire is not None:
        assert fire.direction in ("LONG", "SHORT")
        assert 0.0 <= fire.strength <= 1.0
