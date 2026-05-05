"""Tests for breakaway_gap_up chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.breakaway_gap_up import BreakawayGapUpPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bgu_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = BreakawayGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bgu_id_and_type() -> None:
    p = BreakawayGapUpPattern()
    assert p.pattern_id == "breakaway_gap_up"
    assert p.pattern_type == "chart"


def test_bgu_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 50
    # Massive gap up
    rows.append((110.0, 112.0, 109.0, 111.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = BreakawayGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
