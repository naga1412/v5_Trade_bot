"""Tests for island_reversal_bottom chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.island_reversal_bottom import (
    IslandReversalBottomPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_island_bottom_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = IslandReversalBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_island_bottom_id_and_type() -> None:
    p = IslandReversalBottomPattern()
    assert p.pattern_id == "island_reversal_bottom"
    assert p.pattern_type == "chart"


def test_island_bottom_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0, 1000.0)] * 30
    rows.append((90.0, 91.0, 88.0, 89.0, 1000.0))    # gap down
    rows.append((89.0, 90.0, 87.0, 88.0, 1000.0))    # island
    rows.append((88.0, 89.5, 87.5, 89.0, 1000.0))    # island
    rows.append((89.0, 90.5, 88.5, 90.0, 1000.0))    # island
    rows.append((100.0, 101.0, 99.5, 100.5, 1000.0)) # gap up (open>prev high)
    bars = from_ohlcv(rows)
    fire = IslandReversalBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
