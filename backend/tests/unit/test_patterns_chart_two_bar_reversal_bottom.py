"""Tests for two_bar_reversal_bottom chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.two_bar_reversal_bottom import (
    TwoBarReversalBottomPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_tbrb_no_fire_on_flat() -> None:
    bars = flat_bars(30)
    fire = TwoBarReversalBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_tbrb_id_and_type() -> None:
    p = TwoBarReversalBottomPattern()
    assert p.pattern_id == "two_bar_reversal_bottom"
    assert p.pattern_type == "chart"


def test_tbrb_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0, 1000.0)] * 20
    rows.append((100.0, 100.0, 90.0, 90.0, 1000.0))
    rows.append((90.0, 100.0, 90.0, 100.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = TwoBarReversalBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
