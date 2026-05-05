"""Tests for channel_down chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.channel_down import ChannelDownPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_cd_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = ChannelDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_cd_id_and_type() -> None:
    p = ChannelDownPattern()
    assert p.pattern_id == "channel_down"
    assert p.pattern_type == "chart"


def test_cd_fires_on_synthetic() -> None:
    rng = np.random.default_rng(0)
    n = 70
    closes = np.linspace(120.0, 100.0, n) + rng.uniform(-1.0, 1.0, n)
    bars = from_closes(closes)
    fire = ChannelDownPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
