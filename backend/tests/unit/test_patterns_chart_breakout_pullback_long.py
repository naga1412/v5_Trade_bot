"""Tests for breakout_pullback_long chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.breakout_pullback_long import (
    BreakoutPullbackLongPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bpl_no_fire_on_flat() -> None:
    bars = flat_bars(70)
    fire = BreakoutPullbackLongPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bpl_id_and_type() -> None:
    p = BreakoutPullbackLongPattern()
    assert p.pattern_id == "breakout_pullback_long"
    assert p.pattern_type == "chart"


def test_bpl_fires_on_synthetic() -> None:
    rows = []
    # First 25 bars hover at 100 (resistance ≈ 102)
    for _ in range(25):
        rows.append((100.0, 102.0, 99.0, 100.5, 1000.0))
    # Next 20 bars break out to 110
    for _ in range(20):
        rows.append((105.0, 110.0, 104.0, 109.0, 1000.0))
    # Last 6 bars pull back to ~102 then close above
    for _ in range(5):
        rows.append((105.0, 106.0, 102.0, 104.0, 1000.0))
    rows.append((103.0, 105.0, 102.0, 104.5, 1000.0))  # tags resistance, closes above
    bars = from_ohlcv(rows)
    fire = BreakoutPullbackLongPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
