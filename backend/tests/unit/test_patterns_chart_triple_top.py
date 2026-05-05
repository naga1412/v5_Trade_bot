"""Tests for triple_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.triple_top import TripleTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_triple_top_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = TripleTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_triple_top_id_and_type() -> None:
    p = TripleTopPattern()
    assert p.pattern_id == "triple_top"
    assert p.pattern_type == "chart"


def test_triple_top_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(80, 105, 15))
        + list(np.linspace(105, 95, 8))
        + list(np.linspace(95, 104.5, 10))
        + list(np.linspace(104.5, 95, 8))
        + list(np.linspace(95, 104, 10))
        + list(np.linspace(104, 90, 30))
    )
    bars = from_closes(closes)
    fire = TripleTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
