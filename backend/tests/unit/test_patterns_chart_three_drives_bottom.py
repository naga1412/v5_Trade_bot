"""Tests for three_drives_bottom chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.three_drives_bottom import ThreeDrivesBottomPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_tdb_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = ThreeDrivesBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_tdb_id_and_type() -> None:
    p = ThreeDrivesBottomPattern()
    assert p.pattern_id == "three_drives_bottom"
    assert p.pattern_type == "chart"


def test_tdb_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(120.0, 100.0, 10))
        + list(np.linspace(100.0, 110.0, 6))
        + list(np.linspace(110.0, 95.0, 10))
        + list(np.linspace(95.0, 105.0, 6))
        + list(np.linspace(105.0, 90.0, 10))
        + list(np.linspace(90.0, 100.0, 19))
    )
    bars = from_closes(closes)
    fire = ThreeDrivesBottomPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
