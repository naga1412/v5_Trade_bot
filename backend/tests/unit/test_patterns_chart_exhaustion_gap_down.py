"""Tests for exhaustion_gap_down chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.exhaustion_gap_down import ExhaustionGapDownPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_egd_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = ExhaustionGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_egd_id_and_type() -> None:
    p = ExhaustionGapDownPattern()
    assert p.pattern_id == "exhaustion_gap_down"
    assert p.pattern_type == "chart"


def test_egd_fires_on_synthetic() -> None:
    rows = []
    for i in range(50):
        c = 130.0 - i
        rows.append((c + 0.5, c + 1.0, c - 0.5, c, 1000.0))
    rows.append((70.0, 85.0, 65.0, 84.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = ExhaustionGapDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
