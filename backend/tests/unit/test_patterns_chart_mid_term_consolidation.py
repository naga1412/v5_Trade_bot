"""Tests for mid_term_consolidation chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.mid_term_consolidation import (
    MidTermConsolidationPattern,
)
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_mtc_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = MidTermConsolidationPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_mtc_id_and_type() -> None:
    p = MidTermConsolidationPattern()
    assert p.pattern_id == "mid_term_consolidation"
    assert p.pattern_type == "chart"


def test_mtc_fires_on_synthetic() -> None:
    closes = list(np.linspace(80.0, 120.0, 31)) + [120.0] * 30
    bars = from_closes(closes)
    fire = MidTermConsolidationPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
