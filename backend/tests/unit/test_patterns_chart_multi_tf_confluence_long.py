"""Tests for multi_tf_confluence_long chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.multi_tf_confluence_long import (
    MultiTfConfluenceLongPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_mcl_no_fire_on_flat() -> None:
    bars = flat_bars(220)
    fire = MultiTfConfluenceLongPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_mcl_id_and_type() -> None:
    p = MultiTfConfluenceLongPattern()
    assert p.pattern_id == "multi_tf_confluence_long"
    assert p.pattern_type == "chart"


def test_mcl_fires_on_synthetic() -> None:
    closes = list(np.linspace(80.0, 130.0, 220))
    bars = from_closes(closes)
    fire = MultiTfConfluenceLongPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
