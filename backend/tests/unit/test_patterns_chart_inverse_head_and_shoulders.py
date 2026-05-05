"""Tests for inverse_head_and_shoulders chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.inverse_head_and_shoulders import (
    InverseHeadAndShouldersPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_inv_hs_no_fire_on_flat() -> None:
    bars = flat_bars(100)
    fire = InverseHeadAndShouldersPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_inv_hs_id_and_type() -> None:
    p = InverseHeadAndShouldersPattern()
    assert p.pattern_id == "inverse_head_and_shoulders"
    assert p.pattern_type == "chart"


def test_inv_hs_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(110, 90, 12))
        + list(np.linspace(90, 100, 8))
        + list(np.linspace(100, 80, 12))
        + list(np.linspace(80, 100, 12))
        + list(np.linspace(100, 90, 12))
        + list(np.linspace(90, 110, 25))
    )
    bars = from_closes(closes)
    fire = InverseHeadAndShouldersPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
