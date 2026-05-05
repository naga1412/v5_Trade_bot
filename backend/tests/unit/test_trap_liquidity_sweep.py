"""Tests for liquidity_sweep trap (extreme severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.liquidity_sweep import LiquiditySweepTrap
from app.core.scoring.types import Direction


def _zigzag_bars(n: int = 60) -> pd.DataFrame:
    """Make a zig-zag price series so swing pivots exist."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    base = 100.0
    closes = []
    for i in range(n):
        # alternate small zig-zag to seed swing pivots
        offset = 2.0 if (i // 4) % 2 == 0 else -2.0
        closes.append(base + offset + 0.05 * i)
    close = np.array(closes, dtype=float)
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


def test_liquidity_sweep_metadata() -> None:
    trap = LiquiditySweepTrap()
    assert trap.trap_id == "liquidity_sweep"
    assert trap.severity == "extreme"
    assert trap.side == "both"


def test_liquidity_sweep_returns_none_on_neutral_input() -> None:
    trap = LiquiditySweepTrap()
    bars = _zigzag_bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    # No huge spike → no sweep
    assert result is None


def test_liquidity_sweep_returns_none_when_insufficient_bars() -> None:
    trap = LiquiditySweepTrap()
    bars = _zigzag_bars(n=10)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_liquidity_sweep_fires_on_long_chasing_sweep() -> None:
    trap = LiquiditySweepTrap()
    bars = _zigzag_bars().copy()
    # Inject a giant spike on the last bar so its high overshoots prior swing high
    last = len(bars) - 1
    prior_max = float(bars["high"].iloc[:-1].max())
    bars.iloc[last, bars.columns.get_loc("high")] = prior_max + 50.0
    bars.iloc[last, bars.columns.get_loc("close")] = prior_max + 30.0

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "liquidity_sweep"
    assert result.severity == "extreme"
    assert result.evidence["pierce"] > 0
    assert result.evidence["last_high"] > result.evidence["swept_level"]


def test_liquidity_sweep_does_not_fire_when_direction_opposes_sweep() -> None:
    trap = LiquiditySweepTrap()
    bars = _zigzag_bars().copy()
    last = len(bars) - 1
    prior_max = float(bars["high"].iloc[:-1].max())
    bars.iloc[last, bars.columns.get_loc("high")] = prior_max + 50.0

    ctx = TrapContext()
    # Up-sweep occurred but proposing SHORT — that's a *reversal* trade, not chasing
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None
