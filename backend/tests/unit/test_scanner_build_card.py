"""Unit tests for app/api/routes/scanner.py::_build_card.

The crashing-on-float-final regression flooded production with 500s on
2026-05-12 (scanner endpoint logged ~743 errors in one hour). The root
cause was that _build_extras stores layer_scores["final"] as a SCALAR
FLOAT (the final_score number), while _build_card was doing
``ls.get("final") or {}`` followed by ``.get("direction")`` — the
non-zero float bypassed the ``or {}`` fallback and crashed with
AttributeError.

The fix reads direction/final_score/confidence from the top-level
predictions columns and only falls back to layer_scores["final"]
WHEN IT'S A DICT. These tests pin that contract.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes.scanner import _build_card


def _row(**kw: Any) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking the SQL row shape."""
    defaults: dict[str, Any] = {
        "symbol": "BTC/USDT",
        "ts": "2026-05-12T08:00:00Z",
        "layer_scores": {},
        "direction": None,
        "final_score": None,
        "confidence": None,
        "full_name": "Bitcoin",
        "sparkline": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_build_card_with_float_final_does_NOT_crash() -> None:
    """The exact regression. layer_scores["final"] is a float; row has
    direction/final_score/confidence top-level columns populated."""
    ls = {
        "final": 0.42,  # ← scalar float — used to crash _build_card
        "tier": "PAPER",
        "1": {"direction": "LONG", "strength": 0.6, "confidence": 0.7,
              "notes": "Wyckoff: Markup"},
    }
    card = _build_card(_row(
        layer_scores=json.dumps(ls),
        direction="LONG",
        final_score=0.42,
        confidence=0.7,
    ))
    assert card is not None
    assert card.direction == "LONG"
    assert card.ai_score == 42  # 0.42 * 100


def test_build_card_falls_back_to_dict_final_when_top_level_null() -> None:
    """Old data: layer_scores['final'] is a DICT, top-level columns NULL.

    Should still produce a card (backwards compatibility path)."""
    ls = {
        "final": {"score": 0.55, "direction": "SHORT", "confidence": 0.6},
        "tier": "STANDARD",
    }
    card = _build_card(_row(layer_scores=json.dumps(ls)))
    assert card is not None
    assert card.direction == "SHORT"
    assert card.ai_score == 55


def test_build_card_returns_none_for_neutral_top_level() -> None:
    """NEUTRAL rows are filtered out — _build_card returns None."""
    card = _build_card(_row(
        direction="NEUTRAL",
        final_score=0.05,
        confidence=0.3,
        layer_scores=json.dumps({}),
    ))
    assert card is None


def test_build_card_returns_none_when_no_direction_anywhere() -> None:
    """No top-level direction AND no dict final → NEUTRAL → None."""
    card = _build_card(_row(layer_scores=json.dumps({"final": 0.0})))
    assert card is None


def test_build_card_tolerates_non_dict_layer_entries() -> None:
    """Defensive: ls['1'] might be a scalar (post-extras-merge corruption).

    Must not raise — just falls back to 0 for that layer's contribution."""
    ls = {
        "final": 0.3,
        "tier": "PAPER",
        "1": 0.5,  # ← scalar instead of dict
        "4": "not-a-dict",
    }
    card = _build_card(_row(
        layer_scores=json.dumps(ls),
        direction="LONG",
        final_score=0.3,
        confidence=0.6,
    ))
    assert card is not None
    assert card.scores.smc == 0  # ls["4"] was bogus → 0
    assert card.scores.wyckoff == 0  # ls["1"] was bogus → 0


def test_build_card_tolerates_non_string_tier() -> None:
    """tier coming back as a number / None / list must coerce to NO_SIGNAL."""
    ls = {
        "final": 0.3,
        "tier": 42,  # ← bogus
    }
    card = _build_card(_row(
        layer_scores=json.dumps(ls),
        direction="LONG",
        final_score=0.3,
        confidence=0.6,
    ))
    assert card is not None
    assert card.signal_tier == "NO_SIGNAL"


@pytest.mark.parametrize("bad", [None, "string", 1.5, [1, 2], 0])
def test_build_card_safely_handles_arbitrary_final_garbage(bad: Any) -> None:
    """Reproduce the production crash with the exact value type that broke us."""
    ls: dict[str, Any] = {"final": bad}
    card = _build_card(_row(
        layer_scores=json.dumps(ls),
        direction="LONG",
        final_score=0.2,
        confidence=0.5,
    ))
    # Either a valid card OR None (NEUTRAL) — but NEVER an exception.
    assert card is None or card.direction == "LONG"
