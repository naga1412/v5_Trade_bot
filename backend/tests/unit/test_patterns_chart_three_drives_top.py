"""Tests for three_drives_top chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.three_drives_top import ThreeDrivesTopPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_tdt_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = ThreeDrivesTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_tdt_id_and_type() -> None:
    p = ThreeDrivesTopPattern()
    assert p.pattern_id == "three_drives_top"
    assert p.pattern_type == "chart"


def test_tdt_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(80.0, 100.0, 10))
        + list(np.linspace(100.0, 90.0, 6))
        + list(np.linspace(90.0, 105.0, 10))
        + list(np.linspace(105.0, 95.0, 6))
        + list(np.linspace(95.0, 110.0, 10))
        + list(np.linspace(110.0, 100.0, 19))
    )
    bars = from_closes(closes)
    fire = ThreeDrivesTopPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
