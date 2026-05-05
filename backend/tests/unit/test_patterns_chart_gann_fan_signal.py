"""Tests for gann_fan_signal chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.gann_fan_signal import GannFanSignalPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_gfs_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = GannFanSignalPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_gfs_id_and_type() -> None:
    p = GannFanSignalPattern()
    assert p.pattern_id == "gann_fan_signal"
    assert p.pattern_type == "chart"


def test_gfs_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(110.0, 100.0, 30))     # decline
        + list(np.linspace(100.0, 110.0, 20))   # rebound (peak ~50)
        + list(np.linspace(110.0, 90.0, 20))    # sharp drop to trough at idx ~69
        + [91.0, 91.5, 92.0, 92.5, 93.0]        # base (idx 70-74)
        + [93.5, 94.0, 94.5, 95.0, 95.5, 96.0]  # idx 75-80
        + [105.0]                                # idx 81: jump above line
    )
    bars = from_closes(closes)
    fire = GannFanSignalPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
