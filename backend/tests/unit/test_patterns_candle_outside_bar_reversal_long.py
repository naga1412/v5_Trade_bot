"""Tests for outside_bar_reversal_long — outside-bar with bullish close in oversold context."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.outside_bar_reversal_long import (
    OutsideBarReversalLongPattern,
)


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_outside_bar_reversal_long_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = OutsideBarReversalLongPattern().detect(bars, current_idx=29)
    assert fire is None


def test_outside_bar_reversal_long_pattern_id_and_type() -> None:
    p = OutsideBarReversalLongPattern()
    assert p.pattern_id == "outside_bar_reversal_long"
    assert p.pattern_type == "candle"


def test_outside_bar_reversal_long_fires_in_oversold_context() -> None:
    # 30-bar steady downtrend pushes RSI below 30, then an outside bullish bar.
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0 - i * 0.5, 100.5 - i * 0.5, 99.0 - i * 0.5, 99.0 - i * 0.5, 1_000.0)
        for i in range(30)
    ]
    # Prior bar: small range (high 86, low 85.5).
    rows.append((86.0, 86.0, 85.5, 85.5, 1_000.0))
    # Current bar: outside (high 87 > 86, low 84 < 85.5), bullish close 86.5.
    rows.append((85.5, 87.0, 84.0, 86.5, 1_000.0))
    bars = _bars(rows)
    fire = OutsideBarReversalLongPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
