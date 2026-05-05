"""Tests for key_reversal_short — new high + close below prior low."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.key_reversal_short import KeyReversalShortPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_key_reversal_short_returns_none_on_neutral_input() -> None:
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 10)
    fire = KeyReversalShortPattern().detect(bars, current_idx=9)
    assert fire is None


def test_key_reversal_short_pattern_id_and_type() -> None:
    p = KeyReversalShortPattern()
    assert p.pattern_id == "key_reversal_short"
    assert p.pattern_type == "candle"


def test_key_reversal_short_fires_on_new_high_and_close_below_prior_low() -> None:
    rows: list[tuple[float, float, float, float, float]] = [
        (100.0, 101.0, 99.0, 100.0, 1_000.0)
    ] * 5
    rows.append((100.0, 105.0, 98.0, 104.5, 1_000.0))  # prior bar, high 105
    rows.append((104.0, 110.0, 95.0, 96.0, 1_000.0))  # new high 110, close 96 < 98
    bars = _bars(rows)
    fire = KeyReversalShortPattern().detect(bars, current_idx=len(rows) - 1)
    assert fire is not None
    assert fire.direction == "SHORT"
