"""Tests for key_reversal_low chart pattern."""
from __future__ import annotations

from app.core.patterns.chart.key_reversal_low import KeyReversalLowPattern
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_krl_no_fire_on_flat() -> None:
    bars = flat_bars(40)
    fire = KeyReversalLowPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_krl_id_and_type() -> None:
    p = KeyReversalLowPattern()
    assert p.pattern_id == "key_reversal_low"
    assert p.pattern_type == "chart"


def test_krl_fires_on_synthetic() -> None:
    rows = [(100.0, 101.0, 99.0, 99.5, 1000.0)] * 20
    rows.append((99.5, 100.0, 99.0, 99.5, 1000.0))   # prior bar
    rows.append((99.5, 102.0, 95.0, 101.0, 1000.0))  # new low, close>prior high
    bars = from_ohlcv(rows)
    fire = KeyReversalLowPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
