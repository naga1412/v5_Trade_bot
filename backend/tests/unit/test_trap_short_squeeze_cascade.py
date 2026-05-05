"""Tests for short_squeeze_cascade trap (high severity, short-only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.short_only.short_squeeze_cascade import (
    ShortSqueezeCascadeTrap,
)
from app.core.scoring.types import Direction


def _bars(n: int = 60) -> pd.DataFrame:
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


def _with_squeeze(bars: pd.DataFrame) -> pd.DataFrame:
    """Carve last 3 bars into a vertical bullish run with bodies > ATR."""
    bars = bars.copy()
    last = len(bars) - 1
    for i in (last - 2, last - 1, last):
        bars.iloc[i, bars.columns.get_loc("open")] = 100.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("close")] = 105.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("high")] = 106.0 + (i - last) * 5.0
        bars.iloc[i, bars.columns.get_loc("low")] = 99.0 + (i - last) * 5.0
    return bars


def test_short_squeeze_cascade_metadata() -> None:
    trap = ShortSqueezeCascadeTrap()
    assert trap.trap_id == "short_squeeze_cascade"
    assert trap.severity == "high"
    assert trap.side == "short"


def test_short_squeeze_cascade_returns_none_when_long_proposed() -> None:
    trap = ShortSqueezeCascadeTrap()
    bars = _with_squeeze(_bars())
    ctx = TrapContext(open_interest_delta_24h=0.30)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_short_squeeze_cascade_returns_none_when_oi_missing() -> None:
    trap = ShortSqueezeCascadeTrap()
    bars = _with_squeeze(_bars())
    ctx = TrapContext()  # no OI data
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_short_squeeze_cascade_fires_on_squeeze_with_oi_growth() -> None:
    trap = ShortSqueezeCascadeTrap()
    bars = _with_squeeze(_bars())
    ctx = TrapContext(open_interest_delta_24h=0.30)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "short_squeeze_cascade"
    assert result.severity == "high"
    assert result.side == "short"
    assert result.evidence["open_interest_delta_24h"] == 0.30
    assert result.evidence["avg_body"] > result.evidence["atr"]
