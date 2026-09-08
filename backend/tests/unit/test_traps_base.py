"""TrapFire dataclass + Trap Protocol + TrapContext — SP-5 Phase A."""
from __future__ import annotations

import logging
from dataclasses import is_dataclass

import pandas as pd
import pytest

from app.core.scoring.traps import ALL_TRAPS
from app.core.scoring.traps import base as traps_base
from app.core.scoring.traps.base import (
    Trap,
    TrapContext,
    TrapFire,
    check_safe,
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


def test_all_traps_phase_c_count() -> None:
    """Phase C ships 12 main traps; Phase D appends 5 short-only (final = 17)."""
    assert len(ALL_TRAPS) == 17


def test_all_traps_have_unique_trap_ids() -> None:
    ids = [t.trap_id for t in ALL_TRAPS]
    assert len(ids) == len(set(ids)), f"duplicate trap_ids: {ids}"


def test_all_traps_implement_protocol() -> None:
    for t in ALL_TRAPS:
        assert isinstance(t, Trap), f"{t!r} does not satisfy Trap protocol"


# ---------------------------------------------------------------------------
# 2026-08-14 remediation work order B2: check_safe -- was a bare
# `except Exception: continue` in run_traps.py's orchestrator, zero
# logging, zero per-trap failure tracking. "A trap detector that silently
# swallows is exactly how 7 of 17 traps could be dead without anyone
# knowing." Same failure class as the flow_features endpoint-swallow
# fixed in PR #423.
# ---------------------------------------------------------------------------

_CTX = TrapContext()


class _RaisingTrap:
    trap_id = "broken_trap"
    severity = "medium"
    side = "both"

    def check(self, bars, *, current_idx, layer_scores, proposed_direction, context):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset_trap_failure_streaks():
    traps_base._clear_trap_failure_streaks_for_tests()
    yield
    traps_base._clear_trap_failure_streaks_for_tests()


def _check_safe(trap) -> TrapFire | None:
    return check_safe(
        trap, pd.DataFrame(), current_idx=0, layer_scores={},
        proposed_direction=Direction.LONG, context=_CTX,
    )


def test_check_safe_returns_none_on_raise_without_bricking(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.scoring.traps.base")
    assert _check_safe(_RaisingTrap()) is None
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_check_safe_isolated_single_failure_does_not_escalate(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.scoring.traps.base")
    for _ in range(5):
        _check_safe(_RaisingTrap())
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_check_safe_systematic_failure_escalates_to_error(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.scoring.traps.base")
    for _ in range(traps_base._CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
        _check_safe(_RaisingTrap())
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected an ERROR-level escalation once the streak hit threshold"
    assert "broken_trap" in error_records[-1].getMessage()
    assert "consecutive" in error_records[-1].getMessage().lower()


def test_check_safe_success_resets_the_failure_streak(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.core.scoring.traps.base")
    threshold = traps_base._CONSECUTIVE_FAILURE_ALERT_THRESHOLD

    class _FlakyTrap:
        trap_id = "flaky_trap"
        severity = "medium"
        side = "both"
        should_raise = True

        def check(self, bars, *, current_idx, layer_scores, proposed_direction, context):
            if self.should_raise:
                raise RuntimeError("boom")
            return None

    flaky = _FlakyTrap()
    for _ in range(threshold - 1):
        _check_safe(flaky)
    flaky.should_raise = False
    _check_safe(flaky)  # success resets this trap's streak
    flaky.should_raise = True
    for _ in range(threshold - 1):
        _check_safe(flaky)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
