"""Tests for bullish_flag chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.bullish_flag import BullishFlagPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_bf_no_fire_on_flat() -> None:
    bars = flat_bars(50)
    fire = BullishFlagPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_bf_id_and_type() -> None:
    p = BullishFlagPattern()
    assert p.pattern_id == "bullish_flag"
    assert p.pattern_type == "chart"


def test_bf_fires_on_synthetic() -> None:
    closes = (
        list(np.linspace(100.0, 130.0, 16))    # pole
        + list(np.linspace(130.0, 127.0, 15))  # flag drifting down
    )
    bars = from_closes(closes)
    fire = BullishFlagPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
