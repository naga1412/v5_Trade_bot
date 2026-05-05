"""Tests for double_gap_continuation_down chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.double_gap_continuation_down import (
    DoubleGapContinuationDownPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_dgcd_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = DoubleGapContinuationDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_dgcd_id_and_type() -> None:
    p = DoubleGapContinuationDownPattern()
    assert p.pattern_id == "double_gap_continuation_down"
    assert p.pattern_type == "chart"


def test_dgcd_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 30
    rows.append((90.0, 92.0, 89.0, 89.5, 1000.0))
    rows.append((80.0, 82.0, 79.0, 79.5, 1000.0))
    bars = from_ohlcv(rows)
    fire = DoubleGapContinuationDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
