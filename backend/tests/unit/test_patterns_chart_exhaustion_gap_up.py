"""Tests for exhaustion_gap_up chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.exhaustion_gap_up import ExhaustionGapUpPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_egu_no_fire_on_flat() -> None:
    bars = flat_bars(60)
    fire = ExhaustionGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_egu_id_and_type() -> None:
    p = ExhaustionGapUpPattern()
    assert p.pattern_id == "exhaustion_gap_up"
    assert p.pattern_type == "chart"


def test_egu_fires_on_synthetic() -> None:
    rows = []
    for i in range(50):
        c = 80.0 + i
        rows.append((c - 0.5, c + 0.5, c - 1.0, c, 1000.0))
    # Last bar: huge gap up but close BELOW open
    rows.append((140.0, 145.0, 130.0, 131.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = ExhaustionGapUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
