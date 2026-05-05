"""Tests for all_indicator_extreme trap (high severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.all_indicator_extreme import AllIndicatorExtremeTrap
from app.core.scoring.traps.base import TrapContext
from app.core.scoring.types import Direction


def _flat_bars(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    # Symmetric oscillation around 100 → RSI hovers near 50, never extreme
    close = 100.0 + np.sin(np.arange(n, dtype=float) * 0.5) * 1.0
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _strong_uptrend_bars(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 100.0 + np.arange(n, dtype=float) * 2.0  # monotonic up → RSI ≈ 100
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _strong_downtrend_bars(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 200.0 - np.arange(n, dtype=float) * 2.0
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_all_indicator_extreme_metadata() -> None:
    trap = AllIndicatorExtremeTrap()
    assert trap.trap_id == "all_indicator_extreme"
    assert trap.severity == "high"
    assert trap.side == "both"


def test_all_indicator_extreme_no_fire_on_flat() -> None:
    trap = AllIndicatorExtremeTrap()
    bars = _flat_bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_all_indicator_extreme_fires_on_long_chasing_overbought() -> None:
    trap = AllIndicatorExtremeTrap()
    bars = _strong_uptrend_bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "all_indicator_extreme"
    assert result.severity == "high"
    assert result.evidence["rsi"] > 80.0


def test_all_indicator_extreme_fires_on_short_chasing_oversold() -> None:
    trap = AllIndicatorExtremeTrap()
    bars = _strong_downtrend_bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.evidence["rsi"] < 20.0


def test_all_indicator_extreme_no_fire_on_short_in_uptrend() -> None:
    trap = AllIndicatorExtremeTrap()
    bars = _strong_uptrend_bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None
