"""Tests for falling_wedge chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.falling_wedge import FallingWedgePattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_fw_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = FallingWedgePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_fw_id_and_type() -> None:
    p = FallingWedgePattern()
    assert p.pattern_id == "falling_wedge"
    assert p.pattern_type == "chart"


def test_fw_fires_on_synthetic() -> None:
    rows = []
    n = 70
    for i in range(n):
        hi_env = 110.0 - 0.4 * i
        lo_env = 100.0 - 0.2 * i
        phase = i % 5
        if phase == 0:
            hi = hi_env
            lo = hi_env - 1.0
        elif phase == 2:
            hi = lo_env + 1.0
            lo = lo_env
        else:
            mid = (hi_env + lo_env) / 2
            hi = mid + 1.0
            lo = mid - 1.0
        op = (hi + lo) / 2
        cl = op + 0.1 * float(np.sin(i))
        rows.append((op, hi, lo, cl, 1000.0))
    bars = from_ohlcv(rows)
    fire = FallingWedgePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
