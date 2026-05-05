"""Tests for rounding_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.rounding_top import RoundingTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_rounding_top_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = RoundingTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rounding_top_id_and_type() -> None:
    p = RoundingTopPattern()
    assert p.pattern_id == "rounding_top"
    assert p.pattern_type == "chart"


def test_rounding_top_fires_on_synthetic() -> None:
    # Inverted parabola
    xs = np.linspace(-1.0, 1.0, 81)
    closes = 100.0 + 20.0 * (1.0 - xs**2)
    bars = from_closes(closes)
    fire = RoundingTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
