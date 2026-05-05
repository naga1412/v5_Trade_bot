"""Tests for friday_weekend trap (high severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.friday_weekend import FridayWeekendTrap
from app.core.scoring.types import Direction


def _bars_at(start: str, n: int = 5) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC")
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


def test_friday_weekend_metadata() -> None:
    trap = FridayWeekendTrap()
    assert trap.trap_id == "friday_weekend"
    assert trap.severity == "high"
    assert trap.side == "both"


def test_friday_weekend_no_fire_on_wednesday_morning() -> None:
    trap = FridayWeekendTrap()
    # 2024-01-03 = Wednesday
    bars = _bars_at("2024-01-03 09:00", n=3)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_friday_weekend_fires_on_friday_late_utc() -> None:
    trap = FridayWeekendTrap()
    # 2024-01-05 = Friday, 21:00 UTC
    bars = _bars_at("2024-01-05 21:00", n=2)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "friday_weekend"
    assert result.severity == "high"
    assert result.evidence["is_friday_late"] is True


def test_friday_weekend_fires_on_saturday_for_short() -> None:
    trap = FridayWeekendTrap()
    # 2024-01-06 = Saturday
    bars = _bars_at("2024-01-06 03:00", n=2)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.SHORT,
        context=ctx,
    )
    assert result is not None
    assert result.evidence["is_weekend"] is True
    assert result.evidence["weekday"] == 5


def test_friday_weekend_no_fire_on_friday_morning() -> None:
    trap = FridayWeekendTrap()
    # 2024-01-05 = Friday but 09:00 UTC (before 20:00 cutoff)
    bars = _bars_at("2024-01-05 09:00", n=2)
    ctx = TrapContext()
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None
