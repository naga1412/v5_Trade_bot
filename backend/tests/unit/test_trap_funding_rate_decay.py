"""Tests for funding_rate_decay trap (high severity, short-only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.short_only.funding_rate_decay import (
    FundingRateDecayTrap,
)
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


def test_funding_rate_decay_metadata() -> None:
    trap = FundingRateDecayTrap()
    assert trap.trap_id == "funding_rate_decay"
    assert trap.severity == "high"
    assert trap.side == "short"


def test_funding_rate_decay_returns_none_when_long_proposed() -> None:
    trap = FundingRateDecayTrap()
    bars = _bars()
    ctx = TrapContext(funding_rate=-0.005)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_funding_rate_decay_returns_none_when_funding_missing() -> None:
    trap = FundingRateDecayTrap()
    bars = _bars()
    ctx = TrapContext()  # no funding data
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_funding_rate_decay_returns_none_when_funding_above_threshold() -> None:
    trap = FundingRateDecayTrap()
    bars = _bars()
    ctx = TrapContext(funding_rate=-0.0005)  # not negative enough
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_funding_rate_decay_fires_on_deeply_negative_funding() -> None:
    trap = FundingRateDecayTrap()
    bars = _bars()
    ctx = TrapContext(funding_rate=-0.0025)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "funding_rate_decay"
    assert result.severity == "high"
    assert result.side == "short"
    assert result.evidence["funding_rate"] == -0.0025
    assert result.evidence["threshold"] == -0.001
