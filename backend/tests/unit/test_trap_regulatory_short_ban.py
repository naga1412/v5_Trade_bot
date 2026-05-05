"""Tests for regulatory_short_ban trap (medium severity, short-only — PLACEHOLDER).

v1: no live regulatory feed; the module exists so Phase E orchestrator can
iterate `ALL_TRAPS` with all 17 entries. SP-9 (news ingest) populates the
real signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.base import TrapContext
from app.core.scoring.traps.short_only.regulatory_short_ban import (
    RegulatoryShortBanTrap,
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


def test_regulatory_short_ban_metadata() -> None:
    trap = RegulatoryShortBanTrap()
    assert trap.trap_id == "regulatory_short_ban"
    assert trap.severity == "medium"
    assert trap.side == "short"


def test_regulatory_short_ban_always_returns_none_v1_placeholder() -> None:
    trap = RegulatoryShortBanTrap()
    bars = _bars()
    ctx = TrapContext()
    for direction in (Direction.LONG, Direction.SHORT, Direction.NEUTRAL):
        result = trap.check(
            bars,
            current_idx=len(bars) - 1,
            layer_scores={},
            proposed_direction=direction,
            context=ctx,
        )
        assert result is None
