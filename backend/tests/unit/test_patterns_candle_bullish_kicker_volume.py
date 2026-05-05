"""Tests for bullish_kicker_volume — kicker pattern with volume confirmation."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.bullish_kicker_volume import (
    BullishKickerVolumePattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_bullish_kicker_volume_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = BullishKickerVolumePattern().detect(bars, current_idx=24)
    assert fire is None


def test_bullish_kicker_volume_pattern_id_and_type() -> None:
    p = BullishKickerVolumePattern()
    assert p.pattern_id == "bullish_kicker_volume"
    assert p.pattern_type == "candle"


def test_bullish_kicker_volume_fires_on_gap_up_with_volume() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 100.5, 99.5, 100.0, 1_000.0)
    ] * 22
    # Prior: bearish bar (open 100, close 95).
    rows.append((100.0, 100.0, 95.0, 95.0, 1_000.0))
    # Current: bullish gap-up (open 100 ≥ prior open 100, close > open), volume 3k.
    rows.append((100.0, 105.0, 100.0, 105.0, 3_000.0))
    bars = _bars(rows)
    fire = BullishKickerVolumePattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
