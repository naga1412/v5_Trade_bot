"""Tests for island_reversal_top chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.island_reversal_top import IslandReversalTopPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_island_top_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = IslandReversalTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_island_top_id_and_type() -> None:
    p = IslandReversalTopPattern()
    assert p.pattern_id == "island_reversal_top"
    assert p.pattern_type == "chart"


def test_island_top_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0, 1000.0)] * 30
    # Insert gap-up bar (open way above prior high)
    rows.append((110.0, 112.0, 109.0, 111.0, 1000.0))  # gap up
    rows.append((111.0, 113.0, 110.0, 112.0, 1000.0))  # island
    rows.append((112.0, 113.0, 110.5, 111.0, 1000.0))  # island
    rows.append((110.5, 111.0, 109.5, 110.0, 1000.0))  # island
    rows.append((100.0, 101.0, 99.5, 100.0, 1000.0))   # gap down (open<prev low)
    bars = from_ohlcv(rows)
    fire = IslandReversalTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
