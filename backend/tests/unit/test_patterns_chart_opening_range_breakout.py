"""Tests for opening_range_breakout chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.opening_range_breakout import (
    OpeningRangeBreakoutPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_orb_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = OpeningRangeBreakoutPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_orb_id_and_type() -> None:
    p = OpeningRangeBreakoutPattern()
    assert p.pattern_id == "opening_range_breakout"
    assert p.pattern_type == "chart"


def test_orb_fires_on_synthetic() -> None:
    closes = list(np.linspace(100.0, 100.5, 5)) + list(np.linspace(101.0, 130.0, 26))
    bars = from_closes(closes)
    fire = OpeningRangeBreakoutPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
