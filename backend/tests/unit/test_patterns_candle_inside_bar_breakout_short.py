"""Tests for inside_bar_breakout_short — inside bar followed by close below prior low."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.inside_bar_breakout_short import (
    InsideBarBreakoutShortPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_inside_bar_breakout_short_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = InsideBarBreakoutShortPattern().detect(bars, current_idx=9)
    assert fire is None


def test_inside_bar_breakout_short_pattern_id_and_type() -> None:
    p = InsideBarBreakoutShortPattern()
    assert p.pattern_id == "inside_bar_breakout_short"
    assert p.pattern_type == "candle"


def test_inside_bar_breakout_short_fires_on_breakout_below_prior_low() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    rows.append((105.0, 110.0, 100.0, 102.0, 1_000.0))  # parent A
    rows.append((102.0, 108.0, 101.0, 104.0, 1_000.0))  # inside B
    rows.append((104.0, 105.0, 95.0, 96.0, 1_000.0))  # breakdown C, close < A.low
    bars = _bars(rows)
    fire = InsideBarBreakoutShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
