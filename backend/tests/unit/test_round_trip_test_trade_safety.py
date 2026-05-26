"""PR-FIX-PR275-FOLLOWUP Task 2 (2026-05-27): unit tests for the
safety-freeze check in `tools/round_trip_test_trade.py`.

The first version of the script used `< 90` on
`HYBRID_AUTO_SCORE_THRESHOLD`, which is bounded by the Pydantic
validator to (0, 1). The check could never pass — every invocation
aborted. Fixed to `< 0.9` (fraction scale). This test pins the
threshold semantics so a future scale-confusion regression fails
loudly in CI.

Only tests the safety-check return shape (no Binance / DB / live
worker dependencies are exercised here).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

# The script imports module-level + lazy-imports via `_safe_import()`.
# We exercise `_run` after monkeypatching the lazy deps.
from tools import round_trip_test_trade as tool


def _stub_deps(*, hybrid_value: float | None) -> dict[str, Any]:
    """Build the dict `_safe_import` returns, with HYBRID set to the test value.

    The safety-freeze check fires BEFORE vault_keys / binance / DB are
    touched, so this is enough to drive the early-return path."""
    settings = MagicMock()
    settings.HYBRID_AUTO_SCORE_THRESHOLD = hybrid_value

    def _get_settings() -> Any:
        return settings

    return {
        "get_settings": _get_settings,
        "get_session_factory": MagicMock(),
        "BinanceLiveClient": MagicMock(),
        "_place_approved_order": MagicMock(),
        "vault_keys": MagicMock(return_value=None),
    }


@pytest.mark.parametrize("freeze_value", [0.9, 0.95, 0.99])
def test_safety_check_passes_for_frozen_thresholds(
    freeze_value: float, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen state (>= 0.9) does NOT abort at the safety-check phase.

    Note: the script will still abort at a later phase (vault not
    loaded), but the safety-freeze gate itself passes. The assertion
    is that the `safety_freeze` abort REASON is not emitted.
    """
    monkeypatch.setattr(
        tool, "_safe_import", lambda: _stub_deps(hybrid_value=freeze_value),
    )
    result = asyncio.run(tool._run())
    # Either succeeded all the way (unlikely with MagicMock'd Binance)
    # OR aborted at a phase AFTER safety_freeze. Either way, NOT
    # safety_freeze.
    if result.get("status") == "abort":
        assert result.get("phase") != "safety_freeze", (
            f"safety check rejected a frozen threshold: {result}"
        )


@pytest.mark.parametrize("normal_value", [0.1, 0.35, 0.5, 0.8, 0.89])
def test_safety_check_aborts_for_normal_thresholds(
    normal_value: float, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal trading state (< 0.9) MUST abort the script before
    any vault / Binance / DB action.

    Reasons: the script is the *frozen-state* validation runbook. If
    HYBRID is at 0.35 the bot is actually auto-trading, and a synthetic
    test trade would compete with real signals + tie up margin."""
    monkeypatch.setattr(
        tool, "_safe_import", lambda: _stub_deps(hybrid_value=normal_value),
    )
    result = asyncio.run(tool._run())
    assert result.get("status") == "abort"
    assert result.get("phase") == "safety_freeze"
    assert f"={normal_value}" in result.get("reason", "")


def test_safety_check_aborts_when_hybrid_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bot's default `HYBRID_AUTO_SCORE_THRESHOLD=None` (no hybrid
    routing) is ALSO a 'not frozen' state — the freeze convention is
    'set to a high value to explicitly block auto'. None is the live
    default; refuse to run a test trade against it."""
    monkeypatch.setattr(
        tool, "_safe_import", lambda: _stub_deps(hybrid_value=None),
    )
    result = asyncio.run(tool._run())
    assert result.get("status") == "abort"
    assert result.get("phase") == "safety_freeze"
