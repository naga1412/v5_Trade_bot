"""Tests for v_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.v_top import VTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_v_top_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = VTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_v_top_id_and_type() -> None:
    p = VTopPattern()
    assert p.pattern_id == "v_top"
    assert p.pattern_type == "chart"


def test_v_top_fires_on_synthetic() -> None:
    closes = list(np.linspace(80.0, 120.0, 18)) + list(np.linspace(120.0, 80.0, 18))
    bars = from_closes(closes)
    fire = VTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
