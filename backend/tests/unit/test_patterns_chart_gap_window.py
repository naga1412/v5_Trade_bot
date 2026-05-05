"""Tests for gap_window chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.gap_window import GapWindowPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_gw_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = GapWindowPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_gw_id_and_type() -> None:
    p = GapWindowPattern()
    assert p.pattern_id == "gap_window"
    assert p.pattern_type == "chart"


def test_gw_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 25
    # Gap up at idx 25
    rows.append((110.0, 112.0, 108.0, 111.0, 1000.0))
    # 5 bars staying above prev_high (102)
    for _ in range(5):
        rows.append((110.0, 112.0, 108.0, 110.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = GapWindowPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
