"""PR-strategy-1: entry_quality gate logic.

Pure unit tests for the duck-typed `open_position_gate`. Both call sites
(shadow worker + dispatcher) pass differently-shaped objects (ShadowSignal
vs SignalProposal) — the gate must reach `.direction` + `.entry_score`
without caring which.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.gates.entry_quality import open_position_gate


def _signal(direction: str, entry_score: float | None = 0.5):
    return SimpleNamespace(direction=direction, entry_score=entry_score)


def _settings(*, min_long: float | None = None, disable_short: bool = False):
    return SimpleNamespace(
        MIN_ENTRY_SCORE_LONG=min_long,
        DISABLE_SHORT_SIGNALS=disable_short,
    )


def test_gate_allows_when_flags_off() -> None:
    d = open_position_gate(_signal("LONG", 0.10), _settings())
    assert d.allow is True
    d = open_position_gate(_signal("SHORT", -0.40), _settings())
    assert d.allow is True


def test_gate_denies_short_when_disabled() -> None:
    d = open_position_gate(_signal("SHORT", -0.40), _settings(disable_short=True))
    assert d.allow is False
    assert d.reason == "short_disabled"


def test_gate_allows_short_when_not_disabled() -> None:
    d = open_position_gate(_signal("SHORT", -0.40), _settings(disable_short=False))
    assert d.allow is True


def test_gate_denies_long_below_threshold() -> None:
    d = open_position_gate(_signal("LONG", 0.35), _settings(min_long=0.36))
    assert d.allow is False
    assert d.reason == "below_long_threshold"


def test_gate_allows_long_at_threshold() -> None:
    d = open_position_gate(_signal("LONG", 0.36), _settings(min_long=0.36))
    assert d.allow is True


def test_gate_allows_long_when_threshold_null() -> None:
    d = open_position_gate(_signal("LONG", -1.0), _settings(min_long=None))
    assert d.allow is True
