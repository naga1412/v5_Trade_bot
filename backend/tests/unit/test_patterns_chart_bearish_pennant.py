"""Tests for bearish_pennant chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bearish_pennant import BearishPennantPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bep_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = BearishPennantPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bep_id_and_type() -> None:
    p = BearishPennantPattern()
    assert p.pattern_id == "bearish_pennant"
    assert p.pattern_type == "chart"


def test_bep_fires_on_synthetic() -> None:
    rows = []
    pole = np.linspace(130.0, 100.0, 15)
    for c in pole:
        rows.append((c + 0.5, c + 0.7, c - 0.5, c, 1000.0))
    n2 = 16
    for i in range(n2):
        hi = 102.0 - 4.0 * (i / (n2 - 1))
        lo = 98.0 + 4.0 * (i / (n2 - 1))
        op = (hi + lo) / 2 - 0.2
        cl = (hi + lo) / 2 + 0.2
        rows.append((op, hi, lo, cl, 1000.0))
    bars = from_ohlcv(rows)
    fire = BearishPennantPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
