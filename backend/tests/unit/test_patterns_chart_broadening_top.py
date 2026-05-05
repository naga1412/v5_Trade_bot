"""Tests for broadening_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.broadening_top import BroadeningTopPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bdt_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = BroadeningTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bdt_id_and_type() -> None:
    p = BroadeningTopPattern()
    assert p.pattern_id == "broadening_top"
    assert p.pattern_type == "chart"


def test_bdt_fires_on_synthetic() -> None:
    rows = []
    n = 70
    for i in range(n):
        amp = 5.0 + 15.0 * (i / (n - 1))
        sin = float(np.sin(i * 0.6))
        c = 150.0 + amp * sin
        rows.append((c - 0.3, c + amp + 0.3, c - amp - 0.3, c, 1000.0))
    # Final bar near top of the broadening range
    rows.append((155.0, 170.0, 154.0, 168.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = BroadeningTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
