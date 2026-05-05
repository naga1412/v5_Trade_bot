"""Tests for inside_bar_breakout_long — inside bar followed by close above prior high."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.inside_bar_breakout_long import (
    InsideBarBreakoutLongPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_inside_bar_breakout_long_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = InsideBarBreakoutLongPattern().detect(bars, current_idx=9)
    assert fire is None


def test_inside_bar_breakout_long_pattern_id_and_type() -> None:
    p = InsideBarBreakoutLongPattern()
    assert p.pattern_id == "inside_bar_breakout_long"
    assert p.pattern_type == "candle"


def test_inside_bar_breakout_long_fires_on_breakout_above_prior_high() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    # Bar A: wide range, high 110, low 100.
    rows.append((100.0, 110.0, 100.0, 105.0, 1_000.0))
    # Bar B (inside): high 108 ≤ 110, low 102 ≥ 100.
    rows.append((105.0, 108.0, 102.0, 104.0, 1_000.0))
    # Bar C: closes 112 > prior-A high 110.
    rows.append((104.0, 113.0, 103.5, 112.0, 1_000.0))
    bars = _bars(rows)
    fire = InsideBarBreakoutLongPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
