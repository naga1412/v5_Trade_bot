"""Tests for saucer_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.saucer_bottom import SaucerBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_sb_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = SaucerBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_sb_id_and_type() -> None:
    p = SaucerBottomPattern()
    assert p.pattern_id == "saucer_bottom"
    assert p.pattern_type == "chart"


def test_sb_fires_on_synthetic() -> None:
    xs = np.linspace(-1.0, 1.0, 81)
    # Shallow concave up
    closes = 100.0 + 5.0 * xs**2
    bars = from_closes(closes)
    fire = SaucerBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
