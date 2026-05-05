"""Tests for wide_range_engulf — engulfing with body- and volume-confirmation."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.wide_range_engulf import WideRangeEngulfPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_wide_range_engulf_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = WideRangeEngulfPattern().detect(bars, current_idx=29)
    assert fire is None


def test_wide_range_engulf_pattern_id_and_type() -> None:
    p = WideRangeEngulfPattern()
    assert p.pattern_id == "wide_range_engulf"
    assert p.pattern_type == "candle"


def test_wide_range_engulf_fires_on_bullish_engulf_with_volume() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 22
    # Prior bar: small bearish, body 1.0, volume 1k.
    rows.append((100.0, 100.5, 98.5, 99.0, 1_000.0))
    # Current bar: large bullish, body 4.0 (>=1.5×prior body), high volume 5k.
    rows.append((98.5, 103.0, 98.5, 102.5, 5_000.0))
    bars = _bars(rows)
    fire = WideRangeEngulfPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
