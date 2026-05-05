"""Tests for breakout_pullback_short chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.breakout_pullback_short import (
    BreakoutPullbackShortPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_bps_no_fire_on_flat() -> None:
    bars = flat_bars(70)
    fire = BreakoutPullbackShortPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bps_id_and_type() -> None:
    p = BreakoutPullbackShortPattern()
    assert p.pattern_id == "breakout_pullback_short"
    assert p.pattern_type == "chart"


def test_bps_fires_on_synthetic() -> None:
    rows = []
    for _ in range(25):
        rows.append((100.0, 101.0, 98.0, 99.5, 1000.0))
    for _ in range(20):
        rows.append((95.0, 96.0, 90.0, 91.0, 1000.0))
    for _ in range(5):
        rows.append((95.0, 98.0, 94.0, 96.0, 1000.0))
    rows.append((97.0, 98.0, 95.0, 95.5, 1000.0))  # tags support, closes below
    bars = from_ohlcv(rows)
    fire = BreakoutPullbackShortPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
