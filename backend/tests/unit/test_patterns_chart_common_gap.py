"""Tests for common_gap chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.common_gap import CommonGapPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_cg_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = CommonGapPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_cg_id_and_type() -> None:
    p = CommonGapPattern()
    assert p.pattern_id == "common_gap"
    assert p.pattern_type == "chart"


def test_cg_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 30
    # Small gap up at last bar
    rows.append((103.0, 104.0, 102.5, 103.5, 1000.0))
    bars = from_ohlcv(rows)
    fire = CommonGapPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
