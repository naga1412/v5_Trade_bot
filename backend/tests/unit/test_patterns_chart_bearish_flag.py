"""Tests for bearish_flag chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bearish_flag import BearishFlagPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_bef_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = BearishFlagPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bef_id_and_type() -> None:
    p = BearishFlagPattern()
    assert p.pattern_id == "bearish_flag"
    assert p.pattern_type == "chart"


def test_bef_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(130.0, 100.0, 16))
        + list(np.linspace(100.0, 103.0, 15))
    )
    bars = from_closes(closes)
    fire = BearishFlagPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
