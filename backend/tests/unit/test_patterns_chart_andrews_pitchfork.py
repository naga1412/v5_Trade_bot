"""Tests for andrews_pitchfork chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.andrews_pitchfork import AndrewsPitchforkPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_apf_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = AndrewsPitchforkPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_apf_id_and_type() -> None:
    p = AndrewsPitchforkPattern()
    assert p.pattern_id == "andrews_pitchfork"
    assert p.pattern_type == "chart"


def test_apf_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(100.0, 110.0, 15))   # rise to P0
        + list(np.linspace(110.0, 95.0, 15))  # P1 (trough)
        + list(np.linspace(95.0, 120.0, 30))  # rise to P2 (higher peak)
        + list(np.linspace(120.0, 115.0, 21)) # current bar
    )
    bars = from_closes(closes)
    fire = AndrewsPitchforkPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
