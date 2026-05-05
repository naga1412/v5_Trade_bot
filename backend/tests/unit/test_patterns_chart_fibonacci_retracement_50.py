"""Tests for fibonacci_retracement_50 chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.fibonacci_retracement_50 import (
    FibonacciRetracement50Pattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_fr50_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = FibonacciRetracement50Pattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_fr50_id_and_type() -> None:
    p = FibonacciRetracement50Pattern()
    assert p.pattern_id == "fibonacci_retracement_50"
    assert p.pattern_type == "chart"


def test_fr50_fires_on_synthetic() -> None:
    # Up swing 90 -> 110, then retrace to 100 (50%)
    closes = (
        list(np.linspace(100.0, 90.0, 15))   # initial dip (swing low)
        + list(np.linspace(90.0, 110.0, 25))  # rally to peak
        + list(np.linspace(110.0, 100.0, 41)) # retrace to 50%
    )
    bars = from_closes(closes)
    fire = FibonacciRetracement50Pattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction in ("LONG", "SHORT")
