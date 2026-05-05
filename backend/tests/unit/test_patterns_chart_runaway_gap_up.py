"""Tests for runaway_gap_up chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.runaway_gap_up import RunawayGapUpPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_rgu_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = RunawayGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rgu_id_and_type() -> None:
    p = RunawayGapUpPattern()
    assert p.pattern_id == "runaway_gap_up"
    assert p.pattern_type == "chart"


def test_rgu_fires_on_synthetic() -> None:
    rows = []
    # Uptrend: 30 bars climbing from 80 to 110
    for i in range(30):
        c = 80.0 + i
        rows.append((c - 0.5, c + 0.5, c - 1.0, c, 1000.0))
    # Gap up with volume
    rows.append((113.0, 115.0, 112.0, 114.5, 5000.0))
    bars = from_ohlcv(rows)
    fire = RunawayGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
