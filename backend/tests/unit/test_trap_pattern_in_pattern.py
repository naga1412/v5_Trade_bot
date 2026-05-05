"""Tests for pattern_in_pattern trap (medium severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.pattern_in_pattern import PatternInPatternTrap
from app.core.scoring.types import Direction, LayerScore


def _bars(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_pattern_in_pattern_metadata() -> None:
    trap = PatternInPatternTrap()
    assert trap.trap_id == "pattern_in_pattern"
    assert trap.severity == "medium"
    assert trap.side == "both"


def test_pattern_in_pattern_no_fire_without_l2_signal() -> None:
    trap = PatternInPatternTrap()
    bars = _bars()
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={2: None},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_pattern_in_pattern_no_fire_when_no_inside_bar() -> None:
    trap = PatternInPatternTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    # Make current bar wider than its predecessors
    bars.iloc[last, bars.columns.get_loc("high")] = 110.0
    bars.iloc[last, bars.columns.get_loc("low")] = 90.0

    l2 = LayerScore(direction=Direction.LONG, strength=0.7, confidence=0.6)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={2: l2},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_pattern_in_pattern_fires_with_l2_and_inside_bar() -> None:
    trap = PatternInPatternTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    # Make a prior bar much larger than current bar
    bars.iloc[last - 2, bars.columns.get_loc("high")] = 110.0
    bars.iloc[last - 2, bars.columns.get_loc("low")] = 90.0
    # Current bar tight
    bars.iloc[last, bars.columns.get_loc("high")] = 100.5
    bars.iloc[last, bars.columns.get_loc("low")] = 99.5

    l2 = LayerScore(direction=Direction.LONG, strength=0.7, confidence=0.6)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={2: l2},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "pattern_in_pattern"
    assert result.severity == "medium"
    # Some bar in the trailing 5 contains the current — first match wins (most recent)
    assert result.evidence["encompassing_idx"] in {last - 1, last - 2}
    assert result.evidence["outer_high"] > result.evidence["current_high"]
    assert result.evidence["outer_low"] < result.evidence["current_low"]
    assert result.evidence["l2_direction"] == "LONG"


def test_pattern_in_pattern_no_fire_when_l2_direction_mismatch() -> None:
    trap = PatternInPatternTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    bars.iloc[last - 2, bars.columns.get_loc("high")] = 110.0
    bars.iloc[last - 2, bars.columns.get_loc("low")] = 90.0
    bars.iloc[last, bars.columns.get_loc("high")] = 100.5
    bars.iloc[last, bars.columns.get_loc("low")] = 99.5

    # L2 says SHORT but trader proposes LONG → trap doesn't apply (no nested pattern)
    l2 = LayerScore(direction=Direction.SHORT, strength=0.7, confidence=0.6)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={2: l2},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None
