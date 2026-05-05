"""Tests for two_bar_reversal_top chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.two_bar_reversal_top import TwoBarReversalTopPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_tbrt_no_fire_on_flat() -> None:
    bars = flat_bars(30)
    fire = TwoBarReversalTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_tbrt_id_and_type() -> None:
    p = TwoBarReversalTopPattern()
    assert p.pattern_id == "two_bar_reversal_top"
    assert p.pattern_type == "chart"


def test_tbrt_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0, 1000.0)] * 20
    rows.append((100.0, 110.0, 100.0, 110.0, 1000.0))  # strong bull
    rows.append((110.0, 110.0, 100.0, 100.0, 1000.0))  # strong bear, equal magnitude
    bars = from_ohlcv(rows)
    fire = TwoBarReversalTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
