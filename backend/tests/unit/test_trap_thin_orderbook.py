"""Tests for thin_orderbook trap (medium severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.thin_orderbook import ThinOrderbookTrap
from app.core.scoring.types import Direction


def _bars(n: int = 800, vol: float = 1000.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": np.full(n, vol),
        },
        index=idx,
    )


def test_thin_orderbook_metadata() -> None:
    trap = ThinOrderbookTrap()
    assert trap.trap_id == "thin_orderbook"
    assert trap.severity == "medium"
    assert trap.side == "both"


def test_thin_orderbook_no_fire_on_normal_volume_no_borrow() -> None:
    trap = ThinOrderbookTrap()
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


def test_thin_orderbook_fires_on_high_borrow_rate() -> None:
    trap = ThinOrderbookTrap()
    bars = _bars(n=30)  # short bars OK — borrow path doesn't need history
    ctx = TrapContext(borrow_rate_pct=8.0)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "thin_orderbook"
    assert result.severity == "medium"
    assert result.evidence["trigger"] == "borrow_rate"
    assert result.evidence["borrow_rate_pct"] == 8.0


def test_thin_orderbook_fires_on_low_recent_volume() -> None:
    trap = ThinOrderbookTrap()
    bars = _bars(n=800).copy()
    last = len(bars) - 1
    # Last 24 bars at very low volume vs 720-bar median (1000)
    bars.iloc[last - 23 : last + 1, bars.columns.get_loc("volume")] = 100.0
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=last,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.evidence["trigger"] == "low_volume"
    assert (
        result.evidence["recent_avg_volume"]
        <= 0.5 * result.evidence["historical_median_volume"]
    )
