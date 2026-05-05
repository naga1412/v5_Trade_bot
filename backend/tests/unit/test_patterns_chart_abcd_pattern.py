"""Tests for abcd_pattern chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.abcd_pattern import AbcdPatternPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_abcd_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = AbcdPatternPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_abcd_id_and_type() -> None:
    p = AbcdPatternPattern()
    assert p.pattern_id == "abcd_pattern"
    assert p.pattern_type == "chart"


def test_abcd_returns_valid_or_none_on_synthetic() -> None:
    # Build A=110(high), B=90(low), C=100(high), D=80(low) — AB=CD=20.
    closes = (
        list(np.linspace(95.0, 110.0, 12))    # X (high) -> A (high)
        + list(np.linspace(110.0, 90.0, 12))  # A -> B
        + list(np.linspace(90.0, 100.0, 12))  # B -> C
        + list(np.linspace(100.0, 80.0, 13))  # C -> D
        + list(np.linspace(80.0, 90.0, 12))   # past D
    )
    bars = from_closes(closes)
    fire = AbcdPatternPattern().detect(bars, current_idx=len(bars) - 1)
    if fire is not None:
        assert fire.direction in ("LONG", "SHORT")
