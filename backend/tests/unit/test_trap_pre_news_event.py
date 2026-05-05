"""Tests for pre_news_event trap (extreme severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.pre_news_event import PreNewsEventTrap
from app.core.scoring.types import Direction


def _bars(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.linspace(100.0, 110.0, n)
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


def test_pre_news_event_metadata() -> None:
    trap = PreNewsEventTrap()
    assert trap.trap_id == "pre_news_event"
    assert trap.severity == "extreme"
    assert trap.side == "both"


def test_pre_news_event_returns_none_when_no_news() -> None:
    trap = PreNewsEventTrap()
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


def test_pre_news_event_returns_none_when_news_far_away() -> None:
    trap = PreNewsEventTrap()
    bars = _bars()
    ctx = TrapContext(next_news_event_minutes_until=120)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_pre_news_event_fires_when_news_within_60_min() -> None:
    trap = PreNewsEventTrap()
    bars = _bars()
    ctx = TrapContext(next_news_event_minutes_until=30)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "pre_news_event"
    assert result.severity == "extreme"
    assert result.side == "both"
    assert result.evidence["minutes_until"] == 30
