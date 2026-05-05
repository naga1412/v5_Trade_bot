"""Tests for head_and_shoulders chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.head_and_shoulders import HeadAndShouldersPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_h_and_s_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = HeadAndShouldersPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_h_and_s_id_and_type() -> None:
    p = HeadAndShouldersPattern()
    assert p.pattern_id == "head_and_shoulders"
    assert p.pattern_type == "chart"


def test_h_and_s_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(80, 100, 12))   # rise to left shoulder
        + list(np.linspace(100, 90, 8))   # dip
        + list(np.linspace(90, 110, 12))  # rise to head
        + list(np.linspace(110, 90, 12))  # dip
        + list(np.linspace(90, 100, 12))  # rise to right shoulder
        + list(np.linspace(100, 80, 25))  # break down
    )
    bars = from_closes(closes)
    fire = HeadAndShouldersPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
