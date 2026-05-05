"""Tests for channel_up chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.channel_up import ChannelUpPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_cu_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = ChannelUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_cu_id_and_type() -> None:
    p = ChannelUpPattern()
    assert p.pattern_id == "channel_up"
    assert p.pattern_type == "chart"


def test_cu_fires_on_synthetic() -> None:
    rng = np.random.default_rng(0)
    n = 70
    closes = np.linspace(100.0, 120.0, n) + rng.uniform(-1.0, 1.0, n)
    bars = from_closes(closes)
    fire = ChannelUpPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
