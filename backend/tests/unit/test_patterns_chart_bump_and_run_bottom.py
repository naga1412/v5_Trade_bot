"""Tests for bump_and_run_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bump_and_run_bottom import BumpAndRunBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_barb_no_fire_on_flat() -> None:
    bars = flat_bars(120)
    fire = BumpAndRunBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_barb_id_and_type() -> None:
    p = BumpAndRunBottomPattern()
    assert p.pattern_id == "bump_and_run_bottom"
    assert p.pattern_type == "chart"


def test_barb_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(150.0, 140.0, 50))   # lead slope = -0.2/bar
        + list(np.linspace(140.0, 100.0, 40)) # bump slope = -1.0/bar
        + list(np.linspace(100.0, 150.0, 11)) # run up
    )
    bars = from_closes(closes)
    fire = BumpAndRunBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
