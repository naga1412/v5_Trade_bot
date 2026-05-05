"""Tests for multi_tf_confluence_short chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.multi_tf_confluence_short import (
    MultiTfConfluenceShortPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_mcs_no_fire_on_flat() -> None:
    bars = flat_bars(220)
    fire = MultiTfConfluenceShortPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_mcs_id_and_type() -> None:
    p = MultiTfConfluenceShortPattern()
    assert p.pattern_id == "multi_tf_confluence_short"
    assert p.pattern_type == "chart"


def test_mcs_fires_on_synthetic() -> None:
    closes = list(np.linspace(130.0, 80.0, 220))
    bars = from_closes(closes)
    fire = MultiTfConfluenceShortPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
