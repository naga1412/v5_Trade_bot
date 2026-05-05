"""Tests for double_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.double_top import DoubleTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_double_top_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = DoubleTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_double_top_id_and_type() -> None:
    p = DoubleTopPattern()
    assert p.pattern_id == "double_top"
    assert p.pattern_type == "chart"


def test_double_top_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(80, 105, 20))
        + list(np.linspace(105, 95, 10))
        + list(np.linspace(95, 104.5, 10))
        + list(np.linspace(104.5, 90, 21))
    )
    bars = from_closes(closes)
    fire = DoubleTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
    assert "peak1_high" in fire.evidence
