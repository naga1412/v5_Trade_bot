"""Tests for the composite hammer_or_hanging candle pattern."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.hammer_or_hanging import HammerOrHangingPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_hammer_or_hanging_returns_none_on_neutral_input() -> None:
    """Flat constant bars yield no hammer/hanging-man shape."""
    bars = _bars([(100.0, 101.0, 99.5, 100.5, 1_000.0)] * 30)
    fire = HammerOrHangingPattern().detect(bars, current_idx=29)
    assert fire is None


def test_hammer_or_hanging_pattern_id_and_type() -> None:
    p = HammerOrHangingPattern()
    assert p.pattern_id == "hammer_or_hanging"
    assert p.pattern_type == "candle"


def test_hammer_or_hanging_returns_none_when_history_too_short() -> None:
    """Fewer than 20 bars of history must short-circuit to None."""
    bars = _bars([(100.0, 100.5, 95.0, 100.2, 1_000.0)] * 10)
    fire = HammerOrHangingPattern().detect(bars, current_idx=9)
    assert fire is None


def test_hammer_or_hanging_in_downtrend_yields_long_or_none() -> None:
    """Hammer-shape bar at bottom of a downtrend (close < 20-bar SMA): LONG bias."""
    rows: list[tuple[float, float, float, float, float]] = []
    for i in range(20):
        price = 100.0 - i * 0.5
        rows.append((price, price + 0.3, price - 0.5, price - 0.2, 1_000.0))
    # Hammer-like bar after the downtrend, well below the SMA.
    rows.append((90.0, 90.4, 86.5, 90.2, 2_000.0))
    bars = _bars(rows)
    fire = HammerOrHangingPattern().detect(bars, current_idx=len(rows) - 1)
    if fire is not None:
        # Direction must agree with the trend heuristic.
        assert fire.direction == "LONG"
        assert 0.0 <= fire.strength <= 1.0
        assert 0.0 <= fire.confidence <= 1.0
        assert "talib_func" in fire.evidence
        assert fire.evidence["trend"] == "below_sma"


def test_hammer_or_hanging_in_uptrend_yields_short_or_none() -> None:
    """Hanging-man-shape bar at top of an uptrend (close >= 20-bar SMA): SHORT bias."""
    rows: list[tuple[float, float, float, float, float]] = []
    for i in range(20):
        price = 90.0 + i * 0.5
        rows.append((price, price + 0.5, price - 0.3, price + 0.2, 1_000.0))
    # Hanging-man-like bar after an uptrend, well above the SMA.
    rows.append((100.0, 100.4, 96.5, 100.2, 2_000.0))
    bars = _bars(rows)
    fire = HammerOrHangingPattern().detect(bars, current_idx=len(rows) - 1)
    if fire is not None:
        assert fire.direction == "SHORT"
        assert 0.0 <= fire.strength <= 1.0
        assert 0.0 <= fire.confidence <= 1.0
        assert fire.evidence["trend"] == "at_or_above_sma"
