"""Tests for inverse_cup_and_handle chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.inverse_cup_and_handle import (
    InverseCupAndHandlePattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_ich_no_fire_on_flat() -> None:
    bars = flat_bars(120)
    fire = InverseCupAndHandlePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_ich_id_and_type() -> None:
    p = InverseCupAndHandlePattern()
    assert p.pattern_id == "inverse_cup_and_handle"
    assert p.pattern_type == "chart"


def test_ich_fires_on_synthetic() -> None:
    # Inverted cup: dome over 80 bars (rim=100); handle: rises to 103 then breaks below 99
    cup_xs = np.linspace(-1.0, 1.0, 80)
    cup = 100.0 + 10.0 * (1.0 - cup_xs**2)
    handle = list(np.linspace(100.0, 103.0, 11)) + list(np.linspace(103.0, 99.0, 10))
    closes = list(cup) + handle
    bars = from_closes(closes)
    fire = InverseCupAndHandlePattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
