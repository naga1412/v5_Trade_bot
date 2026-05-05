"""Tests for saucer_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.saucer_top import SaucerTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_st_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = SaucerTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_st_id_and_type() -> None:
    p = SaucerTopPattern()
    assert p.pattern_id == "saucer_top"
    assert p.pattern_type == "chart"


def test_st_fires_on_synthetic() -> None:
    xs = np.linspace(-1.0, 1.0, 81)
    closes = 100.0 + 5.0 * (1.0 - xs**2)
    bars = from_closes(closes)
    fire = SaucerTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
