"""Tests for volume_no_followthrough trap (high severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.volume_no_followthrough import VolumeNoFollowthroughTrap
from app.core.scoring.types import Direction


def _bars(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close.copy(),
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close + 0.5,  # baseline body of 0.5
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_volume_no_followthrough_metadata() -> None:
    trap = VolumeNoFollowthroughTrap()
    assert trap.trap_id == "volume_no_followthrough"
    assert trap.severity == "high"
    assert trap.side == "both"


def test_volume_no_followthrough_no_fire_on_quiet_bars() -> None:
    trap = VolumeNoFollowthroughTrap()
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


def test_volume_no_followthrough_fires_on_spike_then_doji() -> None:
    trap = VolumeNoFollowthroughTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    prev = last - 1
    # prev bar: huge volume + huge body
    bars.iloc[prev, bars.columns.get_loc("volume")] = 10000.0
    bars.iloc[prev, bars.columns.get_loc("open")] = 100.0
    bars.iloc[prev, bars.columns.get_loc("close")] = 110.0
    # current bar: tiny doji body
    bars.iloc[last, bars.columns.get_loc("open")] = 110.0
    bars.iloc[last, bars.columns.get_loc("close")] = 110.05

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "volume_no_followthrough"
    assert result.severity == "high"
    assert result.evidence["prev_volume"] >= 2.0 * result.evidence["avg_volume"]
    assert result.evidence["current_body"] <= 0.3 * result.evidence["prev_body"]


def test_volume_no_followthrough_no_fire_when_followthrough_bar_strong() -> None:
    trap = VolumeNoFollowthroughTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    prev = last - 1
    bars.iloc[prev, bars.columns.get_loc("volume")] = 10000.0
    bars.iloc[prev, bars.columns.get_loc("open")] = 100.0
    bars.iloc[prev, bars.columns.get_loc("close")] = 110.0
    # current bar: still strong follow-through
    bars.iloc[last, bars.columns.get_loc("open")] = 110.0
    bars.iloc[last, bars.columns.get_loc("close")] = 115.0

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None
