"""Tests for diamond_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.diamond_bottom import DiamondBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_dmb_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = DiamondBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_dmb_id_and_type() -> None:
    p = DiamondBottomPattern()
    assert p.pattern_id == "diamond_bottom"
    assert p.pattern_type == "chart"


def test_dmb_fires_on_synthetic() -> None:
    rows = [(150.0, 151.0, 149.0, 150.0, 1000.0)] * 30
    n = 81
    half = n // 2
    for i in range(half):
        amp = 1.0 + 14.0 * (i / (half - 1))
        sin = float(np.sin(i * 0.7))
        c = 90.0 + amp * sin
        rows.append((c - 0.3, c + amp + 0.3, c - amp - 0.3, c, 1000.0))
    for i in range(n - half):
        amp = 15.0 - 14.0 * (i / (n - half - 1))
        sin = float(np.sin((i + half) * 0.7))
        c = 90.0 + amp * sin
        rows.append((c - 0.3, c + amp + 0.3, c - amp - 0.3, c, 1000.0))
    bars = from_ohlcv(rows)
    fire = DiamondBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
