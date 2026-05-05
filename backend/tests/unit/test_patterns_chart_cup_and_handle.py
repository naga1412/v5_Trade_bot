"""Tests for cup_and_handle chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.cup_and_handle import CupAndHandlePattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_ch_no_fire_on_flat() -> None:
    bars = flat_bars(120)
    fire = CupAndHandlePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_ch_id_and_type() -> None:
    p = CupAndHandlePattern()
    assert p.pattern_id == "cup_and_handle"
    assert p.pattern_type == "chart"


def test_ch_fires_on_synthetic() -> None:
    # Cup: U-shape over 80 bars; handle: gentle drift down over 21 bars
    cup_xs = np.linspace(-1.0, 1.0, 80)
    cup = 100.0 + 10.0 * cup_xs**2
    handle = list(np.linspace(110.0, 107.0, 21))
    closes = list(cup) + handle
    bars = from_closes(closes)
    fire = CupAndHandlePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
