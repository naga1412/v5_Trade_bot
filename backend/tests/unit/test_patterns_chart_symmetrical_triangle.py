"""Tests for symmetrical_triangle chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.symmetrical_triangle import SymmetricalTrianglePattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_st_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = SymmetricalTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_st_id_and_type() -> None:
    p = SymmetricalTrianglePattern()
    assert p.pattern_id == "symmetrical_triangle"
    assert p.pattern_type == "chart"


def test_st_fires_on_synthetic() -> None:
    import numpy as np

    rows = []
    n = 60
    for i in range(n - 1):
        envelope_h = 110.0 - 8.0 * (i / (n - 1))
        envelope_l = 90.0 + 8.0 * (i / (n - 1))
        phase = i % 6
        if phase == 0:
            hi = envelope_h
            lo = envelope_h - 1.0
        elif phase == 3:
            hi = envelope_l + 1.0
            lo = envelope_l
        else:
            mid = (envelope_h + envelope_l) / 2
            hi = mid + 1.0
            lo = mid - 1.0
        op = (lo + hi) / 2
        cl = op + 0.1 * float(np.sin(i))
        rows.append((op, hi, lo, cl, 1000.0))
    rows.append((100.0, 115.0, 99.0, 114.0, 5000.0))
    bars = from_ohlcv(rows)
    fire = SymmetricalTrianglePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction in ("LONG", "SHORT")
