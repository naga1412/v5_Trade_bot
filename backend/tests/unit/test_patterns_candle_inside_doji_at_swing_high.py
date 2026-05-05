"""Tests for inside_doji_at_swing_high — doji inside-bar near a swing high → SHORT bias."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.inside_doji_at_swing_high import (
    InsideDojiAtSwingHighPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_inside_doji_at_swing_high_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = InsideDojiAtSwingHighPattern().detect(bars, current_idx=24)
    assert fire is None


def test_inside_doji_at_swing_high_pattern_id_and_type() -> None:
    p = InsideDojiAtSwingHighPattern()
    assert p.pattern_id == "inside_doji_at_swing_high"
    assert p.pattern_type == "candle"


def test_inside_doji_at_swing_high_fires_on_doji_inside_at_swing_high() -> None:
    # 21 filler bars + parent (wide-range, high = swing high) + doji inside-bar.
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 21
    rows.append((105.0, 110.0, 100.0, 105.5, 1_000.0))  # parent at swing high
    rows.append((107.0, 108.0, 106.5, 107.05, 1_000.0))  # doji inside parent
    bars = _bars(rows)
    fire = InsideDojiAtSwingHighPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
