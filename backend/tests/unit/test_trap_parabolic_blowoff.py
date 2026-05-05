"""Tests for parabolic_blowoff trap (extreme severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.parabolic_blowoff import ParabolicBlowoffTrap
from app.core.scoring.types import Direction


def _flat_bars(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close + 0.05,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_parabolic_blowoff_metadata() -> None:
    trap = ParabolicBlowoffTrap()
    assert trap.trap_id == "parabolic_blowoff"
    assert trap.severity == "extreme"
    assert trap.side == "both"


def test_parabolic_blowoff_returns_none_on_flat_bars() -> None:
    trap = ParabolicBlowoffTrap()
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


def test_parabolic_blowoff_fires_on_long_blowoff() -> None:
    trap = ParabolicBlowoffTrap()
    bars = _flat_bars().copy()
    last = len(bars) - 1
    # Last 3 bars: huge bullish bodies (10x typical 0.4 range)
    for i in (last - 2, last - 1, last):
        bars.iloc[i, bars.columns.get_loc("open")] = 100.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("close")] = 105.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("high")] = 106.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("low")] = 99.0 + (i - last) * 5.0

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "parabolic_blowoff"
    assert result.severity == "extreme"
    assert result.evidence["avg_body"] >= 1.5 * result.evidence["atr"]
    assert result.evidence["n_bars"] == 3


def test_parabolic_blowoff_does_not_fire_when_direction_mismatch() -> None:
    trap = ParabolicBlowoffTrap()
    bars = _flat_bars().copy()
    last = len(bars) - 1
    # Last 3 bars are bullish — but trader proposes SHORT
    for i in (last - 2, last - 1, last):
        bars.iloc[i, bars.columns.get_loc("open")] = 100.0
        bars.iloc[i, bars.columns.get_loc("close")] = 110.0
        bars.iloc[i, bars.columns.get_loc("high")] = 111.0
        bars.iloc[i, bars.columns.get_loc("low")] = 99.0

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None
