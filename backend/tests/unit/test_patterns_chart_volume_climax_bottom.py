"""Tests for volume_climax_bottom chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.volume_climax_bottom import VolumeClimaxBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_vcb_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = VolumeClimaxBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_vcb_id_and_type() -> None:
    p = VolumeClimaxBottomPattern()
    assert p.pattern_id == "volume_climax_bottom"
    assert p.pattern_type == "chart"


def test_vcb_fires_on_synthetic() -> None:
    rows = []
    for i in range(50):
        c = 130.0 - i * 0.2
        rows.append((c + 0.5, c + 1.0, c - 0.5, c, 1000.0))
    rows.append((105.0, 116.0, 100.0, 115.0, 10000.0))
    bars = from_ohlcv(rows)
    fire = VolumeClimaxBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
