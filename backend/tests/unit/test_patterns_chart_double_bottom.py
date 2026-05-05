"""Tests for double_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.double_bottom import DoubleBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_double_bottom_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = DoubleBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_double_bottom_id_and_type() -> None:
    p = DoubleBottomPattern()
    assert p.pattern_id == "double_bottom"
    assert p.pattern_type == "chart"


def test_double_bottom_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(110, 90, 20))
        + list(np.linspace(90, 100, 10))
        + list(np.linspace(100, 90.5, 10))
        + list(np.linspace(90.5, 105, 21))
    )
    bars = from_closes(closes)
    fire = DoubleBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
    assert "trough1_low" in fire.evidence
