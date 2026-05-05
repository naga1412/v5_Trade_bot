"""Tests for harmonic_butterfly chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.harmonic_butterfly import HarmonicButterflyPattern
from tests.unit._chart_test_utils import flat_bars, random_walk


def test_hb_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = HarmonicButterflyPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_hb_id_and_type() -> None:
    p = HarmonicButterflyPattern()
    assert p.pattern_id == "harmonic_butterfly"
    assert p.pattern_type == "chart"


def test_hb_returns_none_or_valid_fire_on_random() -> None:
    # Harmonic patterns are sensitive — a random walk should rarely fire,
    # but if it does, the fire must be valid.
    bars = random_walk(120, seed=7)
    fire = HarmonicButterflyPattern().detect(bars, current_idx=len(bars) - 1)
    if fire is not None:
        assert fire.direction in ("LONG", "SHORT")
        assert 0.0 <= fire.strength <= 1.0
