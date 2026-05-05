"""Tests for expanding_triangle chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.expanding_triangle import ExpandingTrianglePattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_et_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = ExpandingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_et_id_and_type() -> None:
    p = ExpandingTrianglePattern()
    assert p.pattern_id == "expanding_triangle"
    assert p.pattern_type == "chart"


def test_et_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(100.0, 105.0, 8))   # peak1
        + list(np.linspace(105.0, 95.0, 8))  # trough1
        + list(np.linspace(95.0, 110.0, 10)) # peak2
        + list(np.linspace(110.0, 90.0, 10)) # trough2
        + list(np.linspace(90.0, 115.0, 12)) # peak3
        + list(np.linspace(115.0, 85.0, 12)) # trough3
        + list(np.linspace(85.0, 100.0, 21)) # back to mid
    )
    bars = from_closes(closes)
    fire = ExpandingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction in ("LONG", "SHORT")
