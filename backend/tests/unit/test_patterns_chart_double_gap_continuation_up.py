"""Tests for double_gap_continuation_up chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.double_gap_continuation_up import (
    DoubleGapContinuationUpPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_dgcu_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = DoubleGapContinuationUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_dgcu_id_and_type() -> None:
    p = DoubleGapContinuationUpPattern()
    assert p.pattern_id == "double_gap_continuation_up"
    assert p.pattern_type == "chart"


def test_dgcu_fires_on_synthetic() -> None:
    rows = [(100.0, 102.0, 98.0, 100.0, 1000.0)] * 30
    rows.append((110.0, 112.0, 109.0, 111.0, 1000.0))  # gap1
    rows.append((120.0, 122.0, 119.0, 121.0, 1000.0))  # gap2
    bars = from_ohlcv(rows)
    fire = DoubleGapContinuationUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
