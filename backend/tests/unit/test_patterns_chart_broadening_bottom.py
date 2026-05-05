"""Tests for broadening_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.broadening_bottom import BroadeningBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bdb_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = BroadeningBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bdb_id_and_type() -> None:
    p = BroadeningBottomPattern()
    assert p.pattern_id == "broadening_bottom"
    assert p.pattern_type == "chart"


def test_bdb_fires_on_synthetic() -> None:
    # Need historical context where window mean is BELOW overall mid.
    # Build first 30 bars high (~150), then 70 bars expanding around 100.
    rows = [(150.0, 151.0, 149.0, 150.0, 1000.0)] * 30
    n = 70
    for i in range(n):
        amp = 5.0 + 15.0 * (i / (n - 1))
        sin = float(np.sin(i * 0.6))
        c = 100.0 + amp * sin
        rows.append((c - 0.3, c + amp + 0.3, c - amp - 0.3, c, 1000.0))
    bars = from_ohlcv(rows)
    fire = BroadeningBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
