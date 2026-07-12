"""Tests for ascending_triangle chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.ascending_triangle import AscendingTrianglePattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_at_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = AscendingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_at_id_and_type() -> None:
    p = AscendingTrianglePattern()
    assert p.pattern_id == "ascending_triangle"
    assert p.pattern_type == "chart"


def test_at_fires_on_synthetic() -> None:
    # Oscillating shape with rising trough envelope and flat peaks
    rows = []
    n = 60
    resistance = 110.0
    for i in range(n):
        support = 95.0 + (108.0 - 95.0) * (i / (n - 1))
        # Sawtooth: every 6 bars touch resistance, in between dip to support
        phase = i % 6
        if phase == 0:
            hi = resistance
            lo = resistance - 0.5
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
    # Add a breakout bar: close above resistance (110.0) to trigger the signal
    rows.append((110.5, 111.0, 110.0, 111.0, 1500.0))
    bars = from_ohlcv(rows)
    fire = AscendingTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
