"""Tests for unlimited_upside_risk trap (medium severity, short-only)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.short_only.unlimited_upside_risk import (
    UnlimitedUpsideRiskTrap,
)
from app.core.scoring.types import Direction


def _bars(n: int = 400, top_high: float = 200.0) -> pd.DataFrame:
    """n bars with one obvious 365-bar trailing high (top_high) at midpoint."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    # Plant the 365-window high in the middle of the trailing window
    mid = n - 200
    high[mid] = top_high
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_unlimited_upside_risk_metadata() -> None:
    trap = UnlimitedUpsideRiskTrap()
    assert trap.trap_id == "unlimited_upside_risk"
    assert trap.severity == "medium"
    assert trap.side == "short"


def test_unlimited_upside_risk_returns_none_when_long_proposed() -> None:
    trap = UnlimitedUpsideRiskTrap()
    bars = _bars()
    last = len(bars) - 1
    # Push close to within 2% of 365-bar high (200.0)
    bars.iloc[last, bars.columns.get_loc("close")] = 199.0
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_unlimited_upside_risk_returns_none_when_far_from_high() -> None:
    trap = UnlimitedUpsideRiskTrap()
    bars = _bars()  # close is 100.0, high is 200.0 — 50% below
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is None


def test_unlimited_upside_risk_fires_within_2pct_of_365_bar_high() -> None:
    trap = UnlimitedUpsideRiskTrap()
    bars = _bars()
    last = len(bars) - 1
    # Within 2% of trailing high 200.0
    bars.iloc[last, bars.columns.get_loc("close")] = 199.0
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "unlimited_upside_risk"
    assert result.severity == "medium"
    assert result.side == "short"
    assert result.evidence["trailing_high"] == 200.0
    assert result.evidence["last_close"] == 199.0
    assert 0 <= result.evidence["distance_pct"] <= 0.02
