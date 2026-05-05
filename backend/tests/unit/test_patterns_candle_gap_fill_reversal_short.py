"""Tests for gap_fill_reversal_short — gap down filled, direction LONG (despite the name)."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.gap_fill_reversal_short import (
    GapFillReversalShortPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_gap_fill_reversal_short_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = GapFillReversalShortPattern().detect(bars, current_idx=9)
    assert fire is None


def test_gap_fill_reversal_short_pattern_id_and_type() -> None:
    p = GapFillReversalShortPattern()
    assert p.pattern_id == "gap_fill_reversal_short"
    assert p.pattern_type == "candle"


def test_gap_fill_reversal_short_fires_on_gap_down_then_close_above_prior_close() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 100.5, 99.5, 100.0, 1_000.0))  # prior, close 100, low 99.5
    # Current: opens at 95 (gap down below prior low), closes at 101 (> prior close).
    rows.append((95.0, 102.0, 94.0, 101.0, 1_000.0))
    bars = _bars(rows)
    fire = GapFillReversalShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
