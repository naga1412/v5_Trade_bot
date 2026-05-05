"""Tests for bullish_pennant chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bullish_pennant import BullishPennantPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bp_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = BullishPennantPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bp_id_and_type() -> None:
    p = BullishPennantPattern()
    assert p.pattern_id == "bullish_pennant"
    assert p.pattern_type == "chart"


def test_bp_fires_on_synthetic() -> None:
    rows = []
    # Pole: 100 → 130 over 15 bars
    pole = np.linspace(100.0, 130.0, 15)
    for c in pole:
        rows.append((c - 0.5, c + 0.5, c - 0.7, c, 1000.0))
    # Pennant: convergent over 15 bars
    n2 = 16
    for i in range(n2):
        hi = 132.0 - 4.0 * (i / (n2 - 1))
        lo = 128.0 + 4.0 * (i / (n2 - 1))
        op = (hi + lo) / 2 - 0.2
        cl = (hi + lo) / 2 + 0.2
        rows.append((op, hi, lo, cl, 1000.0))
    bars = from_ohlcv(rows)
    fire = BullishPennantPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
