"""Tests for rejection_wick_at_support — long lower wick near a swing low."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.rejection_wick_at_support import (
    RejectionWickAtSupportPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_rejection_wick_at_support_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = RejectionWickAtSupportPattern().detect(bars, current_idx=24)
    assert fire is None


def test_rejection_wick_at_support_pattern_id_and_type() -> None:
    p = RejectionWickAtSupportPattern()
    assert p.pattern_id == "rejection_wick_at_support"
    assert p.pattern_type == "candle"


def test_rejection_wick_at_support_fires_at_swing_low_with_long_lower_wick() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 19
    rows.append((95.0, 96.0, 90.0, 95.5, 1_000.0))  # swing low 90.0
    rows.append((100.0, 100.5, 99.0, 100.0, 1_000.0))
    rows.append((100.0, 100.5, 99.0, 100.0, 1_000.0))
    # Current bar: low 90.2 within 0.5 % of 90, lower wick 5, body 1.
    rows.append((96.0, 96.5, 90.2, 95.0, 1_000.0))
    bars = _bars(rows)
    fire = RejectionWickAtSupportPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
