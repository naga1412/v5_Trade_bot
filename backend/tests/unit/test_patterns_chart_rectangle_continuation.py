"""Tests for rectangle_continuation chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.rectangle_continuation import (
    RectangleContinuationPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_ohlcv


def test_rc_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = RectangleContinuationPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rc_id_and_type() -> None:
    p = RectangleContinuationPattern()
    assert p.pattern_id == "rectangle_continuation"
    assert p.pattern_type == "chart"


def test_rc_fires_on_synthetic() -> None:
    rng = np.random.default_rng(2)
    rows = []
    # Strong uptrend (first 21 bars)
    for c in np.linspace(100.0, 130.0, 21):
        rows.append((c - 0.5, c + 0.5, c - 0.7, c, 1000.0))
    # Tight rectangle around 130 (next 40 bars)
    for _ in range(40):
        c = 130.0 + rng.uniform(-0.5, 0.5)
        rows.append((c, 131.0, 129.0, c, 1000.0))
    bars = from_ohlcv(rows)
    fire = RectangleContinuationPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
