"""Tests for strict_three_white_soldiers — stricter CDL3WHITESOLDIERS variant."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.strict_three_white_soldiers import (
    StrictThreeWhiteSoldiersPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_strict_three_white_soldiers_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = StrictThreeWhiteSoldiersPattern().detect(bars, current_idx=29)
    assert fire is None


def test_strict_three_white_soldiers_pattern_id_and_type() -> None:
    p = StrictThreeWhiteSoldiersPattern()
    assert p.pattern_id == "strict_three_white_soldiers"
    assert p.pattern_type == "candle"


def test_strict_three_white_soldiers_fires_on_three_marubozu_like_bars() -> None:
    # Build 14 quiet bars with TR ≈ 1 to seed ATR ≈ 1.0, then 3 bullish bars
    # with body 5 and tiny wicks (5 / 1 = 5× ATR, well over the 0.7× cutoff).
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 15
    rows.append((100.0, 105.05, 99.95, 105.0, 1_000.0))
    rows.append((105.0, 110.05, 104.95, 110.0, 1_000.0))
    rows.append((110.0, 115.05, 109.95, 115.0, 1_000.0))
    bars = _bars(rows)
    fire = StrictThreeWhiteSoldiersPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
    assert 0.0 <= fire.strength <= 1.0
    assert fire.confidence == 0.8
