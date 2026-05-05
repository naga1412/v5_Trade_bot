"""Tests for fibonacci_retracement_618 chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.fibonacci_retracement_618 import (
    FibonacciRetracement618Pattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_fr618_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = FibonacciRetracement618Pattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_fr618_id_and_type() -> None:
    p = FibonacciRetracement618Pattern()
    assert p.pattern_id == "fibonacci_retracement_618"
    assert p.pattern_type == "chart"


def test_fr618_fires_on_synthetic() -> None:
    # Up swing 90 -> 110, then retrace to ~96.8 (61.8%)
    closes = (
        list(np.linspace(100.0, 90.0, 15))
        + list(np.linspace(90.0, 110.0, 25))
        + list(np.linspace(110.0, 96.8, 41))
    )
    bars = from_closes(closes)
    fire = FibonacciRetracement618Pattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction in ("LONG", "SHORT")
