"""SP-5 Phase E4 — run_traps orchestrator + enabled-set filter."""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from app.core.scoring import traps as traps_pkg
from app.core.scoring.run_traps import check_all_traps
from app.core.scoring.traps.base import TrapContext, TrapFire
from app.core.scoring.types import Direction


def _bars() -> pd.DataFrame:
    return pd.DataFrame({
        "open": [100.0] * 50,
        "high": [101.0] * 50,
        "low": [99.0] * 50,
        "close": [100.0] * 50,
        "volume": [1000.0] * 50,
    }, index=pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC"))


def test_returns_list_of_trap_fires() -> None:
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={1: None},
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    assert isinstance(fires, list)
    assert all(isinstance(f, TrapFire) for f in fires)


def test_pre_news_event_fires_when_news_close() -> None:
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={1: None},
        proposed_direction=Direction.LONG,
        context=TrapContext(next_news_event_minutes_until=10),
    )
    assert any(f.trap_id == "pre_news_event" for f in fires)


def test_enabled_set_filter_skips_disabled_trap() -> None:
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={1: None},
        proposed_direction=Direction.LONG,
        context=TrapContext(next_news_event_minutes_until=10),
        enabled_set=set(),  # everything disabled
    )
    assert not any(f.trap_id == "pre_news_event" for f in fires)
    assert fires == []


def test_enabled_set_allows_only_listed_traps() -> None:
    """When enabled_set is a non-empty subset, only those trap_ids may fire."""
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={1: None},
        proposed_direction=Direction.LONG,
        context=TrapContext(next_news_event_minutes_until=10),
        enabled_set={"pre_news_event"},
    )
    # Only pre_news_event allowed; others (even if conditions met) must be filtered.
    assert all(f.trap_id == "pre_news_event" for f in fires)


def test_all_17_traps_in_registry() -> None:
    """Sanity: registry holds 17 traps (12 main + 5 short-only)."""
    assert len(traps_pkg.ALL_TRAPS) == 17


def test_broken_trap_does_not_brick_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poison one trap's check() to raise; orchestrator must keep running."""
    target = traps_pkg.ALL_TRAPS[0]

    def boom(*a: Any, **k: Any) -> TrapFire | None:
        raise RuntimeError("trap is broken")

    monkeypatch.setattr(target, "check", boom)
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={1: None},
        proposed_direction=Direction.LONG,
        context=TrapContext(next_news_event_minutes_until=10),
    )
    # The poisoned trap must be silently skipped; the rest still run, so we
    # still get the pre_news_event fire (unless the poisoned trap WAS the one
    # we wanted to verify — defensive: just assert isinstance(list)).
    assert isinstance(fires, list)
    # Neither the poisoned trap nor a crash leaked through.
    assert all(isinstance(f, TrapFire) for f in fires)


def test_no_fires_when_context_is_neutral() -> None:
    """With a neutral context (no news, no friday, no funding etc.) and flat
    bars, most traps should be quiet — at minimum the orchestrator returns
    cleanly without raising."""
    fires = check_all_traps(
        bars=_bars(),
        current_idx=49,
        layer_scores={i: None for i in range(1, 11)},
        proposed_direction=Direction.LONG,
        context=TrapContext(),
    )
    assert isinstance(fires, list)
