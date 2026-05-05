"""Tests for TA-Lib candle pattern wrapper."""
from __future__ import annotations

import pandas as pd

from app.core.patterns.candle.separating_lines import SeparatingLinesPattern


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"], index=idx
    )


def test_separating_lines_returns_none_on_neutral_input() -> None:
    """Flat constant bars must not trigger the pattern."""
    bars = _bars([(100.0, 101.0, 99.0, 100.0, 1_000.0)] * 30)
    fire = SeparatingLinesPattern().detect(bars, current_idx=29)
    assert fire is None


def test_separating_lines_pattern_id_and_type() -> None:
    p = SeparatingLinesPattern()
    assert p.pattern_id == "separating_lines"
    assert p.pattern_type == "candle"


def test_separating_lines_fires_on_known_shape_or_returns_none() -> None:
    """Synthetic OHLCV resembling a strong reversal — TA-Lib may or may not
    fire depending on its internal heuristics. Either way, the result must
    be a valid PatternFire (with normalised fields) or None."""
    rows: list[tuple[float, float, float, float, float]] = []
    for i in range(20):
        price = 100.0 - i * 0.5
        rows.append((price, price + 0.5, price - 1.0, price - 0.8, 1_000.0))
    rows.append((90.0, 95.0, 89.5, 94.5, 5_000.0))
    bars = _bars(rows)
    fire = SeparatingLinesPattern().detect(bars, current_idx=len(rows) - 1)
    if fire is not None:
        assert fire.direction in ("LONG", "SHORT")
        assert 0.0 <= fire.strength <= 1.0
        assert 0.0 <= fire.confidence <= 1.0
        assert "talib_output" in fire.evidence
        assert "talib_func" in fire.evidence
