"""Tests for rounding_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.rounding_bottom import RoundingBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_rb_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = RoundingBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rb_id_and_type() -> None:
    p = RoundingBottomPattern()
    assert p.pattern_id == "rounding_bottom"
    assert p.pattern_type == "chart"


def test_rb_fires_on_synthetic() -> None:
    xs = np.linspace(-1.0, 1.0, 81)
    closes = 100.0 + 20.0 * xs**2
    bars = from_closes(closes)
    fire = RoundingBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
