"""Tests for price_extreme trap (medium severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.price_extreme import PriceExtremeTrap
from app.core.scoring.types import Direction


def _bars(n: int = 250) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 100.0 + np.sin(np.arange(n, dtype=float) * 0.1) * 5.0
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


def test_price_extreme_metadata() -> None:
    trap = PriceExtremeTrap()
    assert trap.trap_id == "price_extreme"
    assert trap.severity == "medium"
    assert trap.side == "both"


def test_price_extreme_returns_none_with_insufficient_bars() -> None:
    trap = PriceExtremeTrap()
    bars = _bars(n=100)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_price_extreme_no_fire_in_middle_of_range() -> None:
    trap = PriceExtremeTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    # Force last close to be in the middle of the trailing range
    bars.iloc[last, bars.columns.get_loc("close")] = 100.0
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_price_extreme_fires_on_long_at_top() -> None:
    trap = PriceExtremeTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    # Force the trailing window high to a known value, last close just under
    bars.iloc[last - 50, bars.columns.get_loc("high")] = 200.0
    bars.iloc[last, bars.columns.get_loc("close")] = 199.5  # 0.25% below 200

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "price_extreme"
    assert result.severity == "medium"
    assert result.evidence["side"] == "high"
    assert result.evidence["distance_pct"] <= 0.005


def test_price_extreme_fires_on_short_at_bottom() -> None:
    trap = PriceExtremeTrap()
    bars = _bars().copy()
    last = len(bars) - 1
    bars.iloc[last - 50, bars.columns.get_loc("low")] = 50.0
    bars.iloc[last, bars.columns.get_loc("close")] = 50.2  # 0.4% above 50

    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.evidence["side"] == "low"
    assert result.evidence["distance_pct"] <= 0.005
