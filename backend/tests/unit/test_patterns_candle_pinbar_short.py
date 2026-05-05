"""Tests for pinbar_short — long upper wick rejecting upside, body in lower 25%."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.pinbar_short import PinbarShortPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_pinbar_short_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = PinbarShortPattern().detect(bars, current_idx=24)
    assert fire is None


def test_pinbar_short_pattern_id_and_type() -> None:
    p = PinbarShortPattern()
    assert p.pattern_id == "pinbar_short"
    assert p.pattern_type == "candle"


def test_pinbar_short_fires_on_long_upper_wick_with_body_at_bottom() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 22
    # 20-bar swing low stays at 99.0; current pinbar low 99.5 (well above).
    # Pinbar: open 100.0, high 105.0, low 99.5, close 99.7 → bearish body at bottom, long upper wick.
    rows.append((100.0, 105.0, 99.5, 99.7, 1_000.0))
    bars = _bars(rows)
    fire = PinbarShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"


def test_pinbar_short_does_not_fire_at_swing_low() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 22
    rows.append((90.5, 105.0, 90.0, 90.5, 1_000.0))  # low = swing low
    bars = _bars(rows)
    fire = PinbarShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is None
