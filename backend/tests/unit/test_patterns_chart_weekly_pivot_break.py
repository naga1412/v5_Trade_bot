"""Tests for weekly_pivot_break chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.weekly_pivot_break import WeeklyPivotBreakPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_wpb_no_fire_on_flat() -> None:
    bars = flat_bars(180)
    fire = WeeklyPivotBreakPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_wpb_id_and_type() -> None:
    p = WeeklyPivotBreakPattern()
    assert p.pattern_id == "weekly_pivot_break"
    assert p.pattern_type == "chart"


def test_wpb_fires_on_synthetic() -> None:
    closes = list(np.linspace(100.0, 102.0, 168)) + list(np.linspace(102.0, 130.0, 5))
    bars = from_closes(closes)
    fire = WeeklyPivotBreakPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
