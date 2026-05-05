"""L8 Conv-LSTM hookup - SP-5 Phase B4."""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.scoring.layer8_convlstm import GhostInput, score
from app.core.scoring.types import Direction


def bars_with_close(c: float) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=1, freq="1h", tz="UTC"),
        "open": [c], "high": [c], "low": [c], "close": [c], "volume": [1000.0],
    }).set_index("ts")


def test_returns_none_when_no_ghost() -> None:
    assert score(bars_with_close(100.0), ghost=None) is None


def test_returns_none_when_no_bars() -> None:
    bars = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})
    ghost = GhostInput(ghost_close=102.0, ghost_uncertainty=0.1)
    assert score(bars, ghost=ghost) is None


def test_ghost_above_close_is_long() -> None:
    bars = bars_with_close(100.0)
    ghost = GhostInput(ghost_close=102.0, ghost_uncertainty=0.1)
    s = score(bars, ghost=ghost)
    assert s is not None
    assert s.direction is Direction.LONG
    assert s.strength == pytest.approx(0.2, abs=0.001)
    assert s.confidence == pytest.approx(0.9, abs=0.01)


def test_ghost_below_close_is_short() -> None:
    bars = bars_with_close(100.0)
    ghost = GhostInput(ghost_close=99.0, ghost_uncertainty=0.3)
    s = score(bars, ghost=ghost)
    assert s is not None
    assert s.direction is Direction.SHORT
    assert s.strength == pytest.approx(0.1, abs=0.001)


def test_high_uncertainty_caps_confidence_floor() -> None:
    bars = bars_with_close(100.0)
    ghost = GhostInput(ghost_close=101.0, ghost_uncertainty=2.0)
    s = score(bars, ghost=ghost)
    assert s is not None
    assert s.confidence >= 0.3  # floor


def test_zero_delta_is_neutral() -> None:
    bars = bars_with_close(100.0)
    ghost = GhostInput(ghost_close=100.0, ghost_uncertainty=0.5)
    s = score(bars, ghost=ghost)
    assert s is not None
    assert s.direction is Direction.NEUTRAL
