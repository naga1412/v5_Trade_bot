"""Tests for runaway_gap_down chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.runaway_gap_down import RunawayGapDownPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_rgd_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = RunawayGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rgd_id_and_type() -> None:
    p = RunawayGapDownPattern()
    assert p.pattern_id == "runaway_gap_down"
    assert p.pattern_type == "chart"


def test_rgd_fires_on_synthetic() -> None:
    rows = []
    for i in range(30):
        c = 110.0 - i
        rows.append((c + 0.5, c + 1.0, c - 0.5, c, 1000.0))
    rows.append((77.0, 78.0, 75.0, 76.0, 5000.0))
    bars = from_ohlcv(rows)
    fire = RunawayGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
