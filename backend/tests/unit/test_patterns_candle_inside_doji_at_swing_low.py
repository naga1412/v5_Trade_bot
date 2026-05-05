"""Tests for inside_doji_at_swing_low — doji inside-bar near a swing low → LONG bias."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.inside_doji_at_swing_low import (
    InsideDojiAtSwingLowPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_inside_doji_at_swing_low_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = InsideDojiAtSwingLowPattern().detect(bars, current_idx=24)
    assert fire is None


def test_inside_doji_at_swing_low_pattern_id_and_type() -> None:
    p = InsideDojiAtSwingLowPattern()
    assert p.pattern_id == "inside_doji_at_swing_low"
    assert p.pattern_type == "candle"


def test_inside_doji_at_swing_low_fires_on_doji_inside_at_swing_low() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 19
    # Bar at index 19: parent bar — high 100, low 90 (swing low 90.0).
    rows.append((100.0, 100.0, 90.0, 95.0, 1_000.0))
    rows.append((100.0, 101.0, 99.0, 100.0, 1_000.0))  # filler
    rows.append((100.0, 101.0, 99.0, 100.0, 1_000.0))
    # Current: doji inside-bar of immediate prior bar; prior bar's low 90.5 ≈ swing low 90.
    # Re-arrange: place a prior bar with low close to swing low.
    bars = _bars(rows)
    # Build the last two bars deliberately:
    rows = list(bars.itertuples(index=False, name=None))
    rows[-2] = (95.0, 100.0, 90.2, 91.0, 1_000.0)  # parent at swing-low band, wide range
    rows[-1] = (94.0, 95.0, 93.5, 94.05, 1_000.0)  # doji-ish, inside parent, narrow
    bars = _bars(rows)
    fire = InsideDojiAtSwingLowPattern().detect(bars, current_idx=len(bars) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
