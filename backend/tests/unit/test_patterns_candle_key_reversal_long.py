"""Tests for key_reversal_long — new low + close above prior high."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.key_reversal_long import KeyReversalLongPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_key_reversal_long_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = KeyReversalLongPattern().detect(bars, current_idx=9)
    assert fire is None


def test_key_reversal_long_pattern_id_and_type() -> None:
    p = KeyReversalLongPattern()
    assert p.pattern_id == "key_reversal_long"
    assert p.pattern_type == "candle"


def test_key_reversal_long_fires_on_new_low_and_close_above_prior_high() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 102.0, 95.0, 95.5, 1_000.0))  # prior bar, low 95
    rows.append((95.0, 103.0, 90.0, 102.5, 1_000.0))  # new low 90 < 95, close 102.5 > 102
    bars = _bars(rows)
    fire = KeyReversalLongPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "LONG"
