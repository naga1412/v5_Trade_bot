"""Tests for breakaway_gap_down chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.breakaway_gap_down import BreakawayGapDownPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bgd_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = BreakawayGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bgd_id_and_type() -> None:
    p = BreakawayGapDownPattern()
    assert p.pattern_id == "breakaway_gap_down"
    assert p.pattern_type == "chart"


def test_bgd_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 50
    rows.append((90.0, 91.0, 88.0, 89.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = BreakawayGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
