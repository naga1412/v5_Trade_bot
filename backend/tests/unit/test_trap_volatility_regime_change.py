"""Tests for volatility_regime_change trap (medium severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.volatility_regime_change import (
    VolatilityRegimeChangeTrap,
)
from app.core.scoring.types import Direction


def _bars(n: int = 100, atr_scale: float = 0.5) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + atr_scale,
            "low": close - atr_scale,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_volatility_regime_change_metadata() -> None:
    trap = VolatilityRegimeChangeTrap()
    assert trap.trap_id == "volatility_regime_change"
    assert trap.severity == "medium"
    assert trap.side == "both"


def test_volatility_regime_change_no_fire_on_steady_vol() -> None:
    trap = VolatilityRegimeChangeTrap()
    bars = _bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_volatility_regime_change_returns_none_with_insufficient_bars() -> None:
    trap = VolatilityRegimeChangeTrap()
    bars = _bars(n=30)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_volatility_regime_change_fires_on_atr_spike() -> None:
    trap = VolatilityRegimeChangeTrap()
    bars = _bars(n=100, atr_scale=0.5).copy()
    last = len(bars) - 1
    # Inflate the recent 14 bars' high/low ranges by 5x → cur ATR ~5x avg
    for i in range(last - 13, last + 1):
        bars.iloc[i, bars.columns.get_loc("high")] = 105.0
        bars.iloc[i, bars.columns.get_loc("low")] = 95.0

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "volatility_regime_change"
    assert result.severity == "medium"
    assert result.evidence["ratio"] >= 2.0
