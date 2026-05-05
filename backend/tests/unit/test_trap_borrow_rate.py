"""Tests for borrow_rate trap (high severity, short-only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.short_only.borrow_rate import BorrowRateTrap
from app.core.scoring.types import Direction


def _bars(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
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


def test_borrow_rate_metadata() -> None:
    trap = BorrowRateTrap()
    assert trap.trap_id == "borrow_rate"
    assert trap.severity == "high"
    assert trap.side == "short"


def test_borrow_rate_returns_none_when_long_proposed() -> None:
    trap = BorrowRateTrap()
    bars = _bars()
    ctx = TrapContext(borrow_rate_pct=80.0)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_borrow_rate_returns_none_when_borrow_missing() -> None:
    trap = BorrowRateTrap()
    bars = _bars()
    ctx = TrapContext()  # no borrow rate
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_borrow_rate_returns_none_when_borrow_below_threshold() -> None:
    trap = BorrowRateTrap()
    bars = _bars()
    ctx = TrapContext(borrow_rate_pct=10.0)  # below 50%
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_borrow_rate_fires_on_extreme_borrow_apr() -> None:
    trap = BorrowRateTrap()
    bars = _bars()
    ctx = TrapContext(borrow_rate_pct=75.0)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "borrow_rate"
    assert result.severity == "high"
    assert result.side == "short"
    assert result.evidence["borrow_rate_pct"] == 75.0
    assert result.evidence["threshold"] == 50.0
