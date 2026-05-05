"""Tests for harmonic_bat chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.harmonic_bat import HarmonicBatPattern
from tests.unit._chart_test_utils import flat_bars, random_walk


def test_hbat_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = HarmonicBatPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_hbat_id_and_type() -> None:
    p = HarmonicBatPattern()
    assert p.pattern_id == "harmonic_bat"
    assert p.pattern_type == "chart"


def test_hbat_returns_valid_or_none_on_random() -> None:
    bars = random_walk(120, seed=11)
    fire = HarmonicBatPattern().detect(bars, current_idx=len(bars) - 1)
    if fire is not None:
        assert fire.direction in ("LONG", "SHORT")
        assert 0.0 <= fire.strength <= 1.0
