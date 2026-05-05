"""Tests for tweezer_top_strict — two-bar bearish reversal at matched highs."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.tweezer_top_strict import TweezerTopStrictPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_tweezer_top_strict_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = TweezerTopStrictPattern().detect(bars, current_idx=9)
    assert fire is None


def test_tweezer_top_strict_pattern_id_and_type() -> None:
    p = TweezerTopStrictPattern()
    assert p.pattern_id == "tweezer_top_strict"
    assert p.pattern_type == "candle"


def test_tweezer_top_strict_fires_on_bullish_then_bearish_at_matched_high() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    # Prior bar: bullish, high = 110.0
    rows.append((100.0, 110.0, 99.5, 109.5, 1_000.0))
    # Current bar: bearish, high = 110.02 (within 0.05 % of 110.0)
    rows.append((109.5, 110.02, 100.0, 100.5, 1_000.0))
    bars = _bars(rows)
    fire = TweezerTopStrictPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"


def test_tweezer_top_strict_does_not_fire_when_highs_are_far_apart() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 110.0, 99.5, 109.5, 1_000.0))
    rows.append((109.5, 115.0, 100.0, 100.5, 1_000.0))  # high 5 % away
    bars = _bars(rows)
    fire = TweezerTopStrictPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is None
