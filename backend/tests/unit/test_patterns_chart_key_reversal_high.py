"""Tests for key_reversal_high chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.key_reversal_high import KeyReversalHighPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_krh_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = KeyReversalHighPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_krh_id_and_type() -> None:
    p = KeyReversalHighPattern()
    assert p.pattern_id == "key_reversal_high"
    assert p.pattern_type == "chart"


def test_krh_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 100.5, 1000.0)] * 20
    rows.append((100.5, 100.6, 99.5, 100.0, 1000.0))  # prior bar
    rows.append((100.0, 105.0, 98.0, 99.0, 1000.0))   # new high, close<prior low
    bars = from_ohlcv(rows)
    fire = KeyReversalHighPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
