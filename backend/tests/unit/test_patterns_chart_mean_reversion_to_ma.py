"""Tests for mean_reversion_to_ma chart pattern."""
from __future__ import annotations

import numpy as np

from app.core.patterns.chart.mean_reversion_to_ma import MeanReversionToMaPattern
from tests.unit._chart_test_utils import flat_bars, from_closes


def test_mrm_no_fire_on_flat() -> None:
    bars = flat_bars(80)
    fire = MeanReversionToMaPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is None


def test_mrm_id_and_type() -> None:
    p = MeanReversionToMaPattern()
    assert p.pattern_id == "mean_reversion_to_ma"
    assert p.pattern_type == "chart"


def test_mrm_fires_on_synthetic() -> None:
    # 60 closes near 100, then a wide spike up → SHORT
    closes = list(np.linspace(100.0, 100.5, 60)) + list(np.linspace(101.0, 130.0, 21))
    bars = from_closes(closes)
    fire = MeanReversionToMaPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
