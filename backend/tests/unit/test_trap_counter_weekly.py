"""Tests for counter_weekly trap (high severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.counter_weekly import CounterWeeklyTrap
from app.core.scoring.types import Direction


def _trending_bars(slope: float, n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 100.0 + slope * np.arange(n, dtype=float)
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


def test_counter_weekly_metadata() -> None:
    trap = CounterWeeklyTrap()
    assert trap.trap_id == "counter_weekly"
    assert trap.severity == "high"
    assert trap.side == "both"


def test_counter_weekly_returns_none_when_insufficient_bars() -> None:
    trap = CounterWeeklyTrap()
    bars = _trending_bars(slope=0.5, n=100)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_counter_weekly_no_fire_when_aligned() -> None:
    trap = CounterWeeklyTrap()
    # Uptrend → weekly bias LONG, proposing LONG = aligned, no fire
    bars = _trending_bars(slope=0.5, n=200)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_counter_weekly_fires_on_short_against_uptrend() -> None:
    trap = CounterWeeklyTrap()
    # Strong uptrend → EMA slope positive → weekly LONG; proposing SHORT fires
    bars = _trending_bars(slope=0.5, n=200)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "counter_weekly"
    assert result.severity == "high"
    assert result.evidence["weekly_direction"] == "LONG"
    assert result.evidence["ema_slope"] > 0


def test_counter_weekly_fires_on_long_against_downtrend() -> None:
    trap = CounterWeeklyTrap()
    bars = _trending_bars(slope=-0.5, n=200)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.evidence["weekly_direction"] == "SHORT"
    assert result.evidence["ema_slope"] < 0
