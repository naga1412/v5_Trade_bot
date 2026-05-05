"""Tests for tweezer_bottom_strict — two-bar bullish reversal at matched lows."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.tweezer_bottom_strict import (
    TweezerBottomStrictPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_tweezer_bottom_strict_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = TweezerBottomStrictPattern().detect(bars, current_idx=9)
    assert fire is None


def test_tweezer_bottom_strict_pattern_id_and_type() -> None:
    p = TweezerBottomStrictPattern()
    assert p.pattern_id == "tweezer_bottom_strict"
    assert p.pattern_type == "candle"


def test_tweezer_bottom_strict_fires_on_bearish_then_bullish_at_matched_low() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 100.5, 90.0, 90.5, 1_000.0))  # bearish, low 90.0
    rows.append((90.5, 100.0, 90.02, 99.5, 1_000.0))  # bullish, low 90.02
    bars = _bars(rows)
    fire = TweezerBottomStrictPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"


def test_tweezer_bottom_strict_does_not_fire_when_lows_far_apart() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 100.5, 90.0, 90.5, 1_000.0))
    rows.append((90.5, 100.0, 85.0, 99.5, 1_000.0))  # low 5 % away
    bars = _bars(rows)
    fire = TweezerBottomStrictPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is None
