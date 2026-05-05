"""Tests for end_of_day_reversal chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.end_of_day_reversal import EndOfDayReversalPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_eodr_no_fire_on_flat() -> None:
    bars = flat_bars(30)
    fire = EndOfDayReversalPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_eodr_id_and_type() -> None:
    p = EndOfDayReversalPattern()
    assert p.pattern_id == "end_of_day_reversal"
    assert p.pattern_type == "chart"


def test_eodr_fires_on_synthetic() -> None:
    rows = []
    # Drift up over 20 bars
    for i in range(20):
        c = 100.0 + i
        rows.append((c - 0.3, c + 0.3, c - 0.5, c, 1000.0))
    # Then a sharp bearish bar
    rows.append((120.0, 121.0, 105.0, 106.0, 1000.0))
    bars = from_ohlcv(rows)
    fire = EndOfDayReversalPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
