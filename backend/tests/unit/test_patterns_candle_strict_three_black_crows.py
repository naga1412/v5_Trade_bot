"""Tests for strict_three_black_crows — stricter CDL3BLACKCROWS variant."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.strict_three_black_crows import (
    StrictThreeBlackCrowsPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_strict_three_black_crows_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = StrictThreeBlackCrowsPattern().detect(bars, current_idx=29)
    assert fire is None


def test_strict_three_black_crows_pattern_id_and_type() -> None:
    p = StrictThreeBlackCrowsPattern()
    assert p.pattern_id == "strict_three_black_crows"
    assert p.pattern_type == "candle"


def test_strict_three_black_crows_fires_on_three_marubozu_like_bars() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 15
    rows.append((100.0, 100.05, 94.95, 95.0, 1_000.0))
    rows.append((95.0, 95.05, 89.95, 90.0, 1_000.0))
    rows.append((90.0, 90.05, 84.95, 85.0, 1_000.0))
    bars = _bars(rows)
    fire = StrictThreeBlackCrowsPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
    assert fire.confidence == 0.8
