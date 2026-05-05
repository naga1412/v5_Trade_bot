"""TrapFire dataclass + Trap Protocol + TrapContext — SP-5 Phase A."""
from __future__ import annotations

from dataclasses import is_dataclass

import pandas as pd
import pytest

from app.core.scoring.traps.base import (
    Trap,
    TrapContext,
    TrapFire,
)
from app.core.scoring.types import Direction, LayerScore


def test_trap_fire_is_frozen_dataclass() -> None:
    f = TrapFire(
        trap_id="x",
        severity="medium",
        side="long",
        reason="r",
        evidence={"k": 1},
    )
    assert is_dataclass(f)
    with pytest.raises(Exception):
        f.trap_id = "y"  # type: ignore[misc]


def test_trap_fire_rejects_invalid_severity() -> None:
    with pytest.raises(ValueError):
        TrapFire(trap_id="x", severity="huge", side="long", reason="", evidence={})


def test_trap_fire_rejects_invalid_side() -> None:
    with pytest.raises(ValueError):
        TrapFire(trap_id="x", severity="medium", side="up", reason="", evidence={})


def test_trap_context_default_all_none() -> None:
    ctx = TrapContext()
    assert ctx.next_news_event_minutes_until is None
    assert ctx.is_friday_close is False
    assert ctx.weekly_bias is Direction.NEUTRAL
    assert ctx.btc_atr_pct is None
    assert ctx.funding_rate is None
    assert ctx.open_interest_delta_24h is None
    assert ctx.borrow_rate_pct is None
    assert ctx.symbol == ""
    assert ctx.timeframe == ""


def test_trap_protocol_runtime_check() -> None:
    class FakeTrap:
        trap_id = "fake"
        severity = "medium"
        side = "both"

        def check(
            self, bars: pd.DataFrame, *, current_idx: int,
            layer_scores: dict[int, LayerScore | None],
            proposed_direction: Direction,
            context: TrapContext,
        ) -> TrapFire | None:
            return None

    f = FakeTrap()
    assert isinstance(f, Trap)
