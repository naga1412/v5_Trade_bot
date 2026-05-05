"""Tests for descending_triangle chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.descending_triangle import DescendingTrianglePattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_dt_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = DescendingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_dt_id_and_type() -> None:
    p = DescendingTrianglePattern()
    assert p.pattern_id == "descending_triangle"
    assert p.pattern_type == "chart"


def test_dt_fires_on_synthetic() -> None:
    rows = []
    n = 60
    support = 90.0
    for i in range(n):
        resistance = 105.0 - (105.0 - 92.0) * (i / (n - 1))
        phase = i % 6
        if phase == 0:
            hi = resistance + 0.5
            lo = resistance
        elif phase == 3:
            hi = support + 1.0
            lo = support
        else:
            mid = (resistance + support) / 2
            hi = mid + 1.0
            lo = mid - 1.0
        op = (lo + hi) / 2
        cl = op + 0.1 * float(np.sin(i))
        rows.append((op, hi, lo, cl, 1000.0))
    bars = from_ohlcv(rows)
    fire = DescendingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
