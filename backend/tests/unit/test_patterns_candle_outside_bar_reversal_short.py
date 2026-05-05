"""Tests for outside_bar_reversal_short — outside-bar with bearish close in overbought context."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.outside_bar_reversal_short import (
    OutsideBarReversalShortPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_outside_bar_reversal_short_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = OutsideBarReversalShortPattern().detect(bars, current_idx=29)
    assert fire is None


def test_outside_bar_reversal_short_pattern_id_and_type() -> None:
    p = OutsideBarReversalShortPattern()
    assert p.pattern_id == "outside_bar_reversal_short"
    assert p.pattern_type == "candle"


def test_outside_bar_reversal_short_fires_in_overbought_context() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0 + i * 0.5, 101.0 + i * 0.5, 99.5 + i * 0.5, 101.0 + i * 0.5, 1_000.0)
        for i in range(30)
    ]
    rows.append((115.5, 116.0, 115.5, 115.5, 1_000.0))  # prior small range
    rows.append((115.5, 117.0, 114.0, 114.5, 1_000.0))  # outside bearish
    bars = _bars(rows)
    fire = OutsideBarReversalShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
