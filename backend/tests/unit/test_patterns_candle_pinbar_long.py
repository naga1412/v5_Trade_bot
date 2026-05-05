"""Tests for pinbar_long — long lower wick rejecting downside, body in upper 25%."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.pinbar_long import PinbarLongPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_pinbar_long_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 25)
    fire = PinbarLongPattern().detect(bars, current_idx=24)
    assert fire is None


def test_pinbar_long_pattern_id_and_type() -> None:
    p = PinbarLongPattern()
    assert p.pattern_id == "pinbar_long"
    assert p.pattern_type == "candle"


def test_pinbar_long_fires_on_long_lower_wick_with_body_at_top() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 22
    # 20-bar swing high stays at 101.0; current pinbar high 100.5 (well below).
    # Pinbar: open 100.4, high 100.5, low 95.0, close 100.4 → body in top 25 %, long lower wick.
    rows.append((100.0, 100.5, 95.0, 100.4, 1_000.0))
    bars = _bars(rows)
    fire = PinbarLongPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"


def test_pinbar_long_does_not_fire_at_swing_high() -> None:
    # Last bar is also the swing high — should suppress.
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 22
    rows.append((100.0, 110.0, 95.0, 109.5, 1_000.0))  # high = 110 = swing high
    bars = _bars(rows)
    fire = PinbarLongPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is None
