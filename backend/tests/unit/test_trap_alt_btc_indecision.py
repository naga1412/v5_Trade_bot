"""Tests for alt_btc_indecision trap (high severity)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.scoring.traps.alt_btc_indecision import AltBtcIndecisionTrap
from app.core.scoring.traps.base import TrapContext
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


def test_alt_btc_indecision_metadata() -> None:
    trap = AltBtcIndecisionTrap()
    assert trap.trap_id == "alt_btc_indecision"
    assert trap.severity == "high"
    assert trap.side == "both"


def test_alt_btc_indecision_returns_none_on_btc() -> None:
    trap = AltBtcIndecisionTrap()
    bars = _bars()
    ctx = TrapContext(symbol="BTC/USDT", btc_atr_pct=0.2)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_alt_btc_indecision_returns_none_when_btc_atr_unknown() -> None:
    trap = AltBtcIndecisionTrap()
    bars = _bars()
    ctx = TrapContext(symbol="ETH/USDT", btc_atr_pct=None)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_alt_btc_indecision_no_fire_when_btc_active() -> None:
    trap = AltBtcIndecisionTrap()
    bars = _bars()
    ctx = TrapContext(symbol="ETH/USDT", btc_atr_pct=2.5)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is None


def test_alt_btc_indecision_fires_on_alt_with_btc_consolidating() -> None:
    trap = AltBtcIndecisionTrap()
    bars = _bars()
    ctx = TrapContext(symbol="ETH/USDT", btc_atr_pct=0.3)
    result = trap.check(
        bars,
        current_idx=len(bars) - 1,
        layer_scores={},
        proposed_direction=Direction.LONG,
        context=ctx,
    )
    assert result is not None
    assert result.trap_id == "alt_btc_indecision"
    assert result.severity == "high"
    assert result.evidence["symbol"] == "ETH/USDT"
    assert result.evidence["btc_atr_pct"] == 0.3
