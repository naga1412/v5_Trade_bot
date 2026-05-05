"""Tests for volume_climax_top chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.volume_climax_top import VolumeClimaxTopPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_vct_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = VolumeClimaxTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_vct_id_and_type() -> None:
    p = VolumeClimaxTopPattern()
    assert p.pattern_id == "volume_climax_top"
    assert p.pattern_type == "chart"


def test_vct_fires_on_synthetic() -> None:
    rows = []
    for i in range(50):
        c = 100.0 + i * 0.2
        rows.append((c - 0.5, c + 0.5, c - 1.0, c, 1000.0))
    # Climax: high makes new high but bar closes below open with huge volume
    rows.append((115.0, 120.0, 105.0, 106.0, 10000.0))
    bars = from_ohlcv(rows)
    fire = VolumeClimaxTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
