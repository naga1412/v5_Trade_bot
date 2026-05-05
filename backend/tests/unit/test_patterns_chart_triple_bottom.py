"""Tests for triple_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.triple_bottom import TripleBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_triple_bottom_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = TripleBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_triple_bottom_id_and_type() -> None:
    p = TripleBottomPattern()
    assert p.pattern_id == "triple_bottom"
    assert p.pattern_type == "chart"


def test_triple_bottom_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(110, 90, 15))
        + list(np.linspace(90, 100, 8))
        + list(np.linspace(100, 90.5, 10))
        + list(np.linspace(90.5, 100, 8))
        + list(np.linspace(100, 91, 10))
        + list(np.linspace(91, 105, 30))
    )
    bars = from_closes(closes)
    fire = TripleBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
