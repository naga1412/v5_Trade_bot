"""Tests for rejection_wick_at_resistance — long upper wick near a swing high."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.rejection_wick_at_resistance import (
    RejectionWickAtResistancePattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_rejection_wick_at_resistance_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = RejectionWickAtResistancePattern().detect(bars, current_idx=24)
    assert fire is None


def test_rejection_wick_at_resistance_pattern_id_and_type() -> None:
    p = RejectionWickAtResistancePattern()
    assert p.pattern_id == "rejection_wick_at_resistance"
    assert p.pattern_type == "candle"


def test_rejection_wick_at_resistance_fires_at_swing_high_with_long_upper_wick() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 19
    rows.append((105.0, 110.0, 104.0, 109.5, 1_000.0))  # swing high 110.0
    rows.append((100.0, 100.5, 99.0, 100.0, 1_000.0))  # filler
    rows.append((100.0, 100.5, 99.0, 100.0, 1_000.0))  # filler
    # Current bar: high 109.8 (within 0.5 % of 110), upper wick 5, body 1 → wick≥2×body.
    rows.append((105.0, 109.8, 104.0, 104.5, 1_000.0))
    bars = _bars(rows)
    fire = RejectionWickAtResistancePattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
