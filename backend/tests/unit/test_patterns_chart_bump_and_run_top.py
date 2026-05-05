"""Tests for bump_and_run_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bump_and_run_top import BumpAndRunTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_bart_no_fire_on_flat() -> None:
    bars = flat_bars(120)
    fire = BumpAndRunTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bart_id_and_type() -> None:
    p = BumpAndRunTopPattern()
    assert p.pattern_id == "bump_and_run_top"
    assert p.pattern_type == "chart"


def test_bart_fires_on_synthetic() -> None:
    # Lead-in slow rise then steep bump then sharp decline
    closes = (
        list(np.linspace(100.0, 110.0, 50))    # lead slope = 0.2/bar
        + list(np.linspace(110.0, 150.0, 40))  # bump slope = 1.0/bar
        + list(np.linspace(150.0, 100.0, 11))  # run down
    )
    bars = from_closes(closes)
    fire = BumpAndRunTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
