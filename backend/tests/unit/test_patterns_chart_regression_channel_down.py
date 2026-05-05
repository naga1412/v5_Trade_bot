"""Tests for regression_channel_down chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.regression_channel_down import (
    RegressionChannelDownPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_rcd_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = RegressionChannelDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_rcd_id_and_type() -> None:
    p = RegressionChannelDownPattern()
    assert p.pattern_id == "regression_channel_down"
    assert p.pattern_type == "chart"


def test_rcd_fires_on_synthetic() -> None:
    rng = np.random.default_rng(0)
    n = 70
    closes = np.linspace(130.0, 100.0, n) + rng.uniform(-1.5, 1.5, n)
    bars = from_closes(closes)
    fire = RegressionChannelDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
