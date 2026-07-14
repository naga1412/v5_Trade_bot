"""Unit tests for the Sharpe gate in register_brain._direct_db_register_and_activate.

The gate logic lives in tools/ml/register_brain.py and delegates the
threshold comparison to app.ml.champion_challenger.sharpe_passes.

Gate branches:
  bootstrap      — no active champion → register + activate unconditionally
  gate_passed    — sharpe_passes(challenger, champion) → activate
  gate_held      — sharpe_passes returns False → register-only
  gate_undecidable — champion has no Sharpe in eval_results → register-only
  force          — --force=True bypasses all gate logic → activate unconditionally
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Import the standalone script from tools/ml/ (not a package).
_TOOLS_ML = Path(__file__).resolve().parents[3] / "tools" / "ml"
if str(_TOOLS_ML) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ML))

import register_brain  # noqa: E402 (after sys.path manipulation)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_PAYLOAD: dict = {
    "model_name": "ppo_policy_v1",
    "version": "v1-20260714-030000",
    "checkpoint_uri": "file:///app/data/rl-cache/test.pt",
    "sha256": "abc" * 21,
    "trained_at": "2026-07-14T03:00:00+00:00",
    "train_data_window": "{}",
    "eval_results": {"sharpe": 1.5, "max_dd": 0.12},
    "notes": None,
}


def _fake_row(**attrs):
    r = mock.MagicMock()
    for k, v in attrs.items():
        setattr(r, k, v)
    return r


def _make_session(execute_returns: list):
    """Async mock session with ordered execute() side-effects.

    Each entry in execute_returns becomes the return value of .first() on
    the corresponding execute() call.
    """
    results = []
    for item in execute_returns:
        res = mock.MagicMock()
        res.first.return_value = item
        results.append(res)

    s = mock.AsyncMock()
    s.execute = mock.AsyncMock(side_effect=results)
    s.commit = mock.AsyncMock()
    s.__aenter__ = mock.AsyncMock(return_value=s)
    s.__aexit__ = mock.AsyncMock(return_value=False)
    return s


def _make_factory(session):
    """Wrap session in the two-layer callable that get_session_factory() returns."""
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=session)
    ctx.__aexit__ = mock.AsyncMock(return_value=False)

    def _sf():
        return ctx

    return mock.MagicMock(return_value=_sf)


def _call_direct(payload=None, *, activate=True, force=False, execute_returns):
    """Invoke _direct_db_register_and_activate with a mocked session.

    The real app.ml.champion_challenger.sharpe_passes is imported by _go() at
    call time (inside the nested function), so no separate mock is required —
    the backend app package is on sys.path in the pytest environment.
    """
    payload = payload or _BASE_PAYLOAD.copy()
    session = _make_session(execute_returns)
    factory = _make_factory(session)

    with mock.patch("app.db.session.get_session_factory", factory):
        return register_brain._direct_db_register_and_activate(
            payload=payload,
            activate_flag=activate,
            force=force,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bootstrap_no_champion_activates():
    """When no active champion exists, register and activate unconditionally."""
    insert_row = _fake_row(id=42, is_active=True)
    result = _call_direct(
        execute_returns=[
            None,        # SELECT champion → no row
            insert_row,  # INSERT RETURNING
        ]
    )

    assert result["id"] == 42
    assert result["is_active"] is True
    assert result["gate_outcome"] == "bootstrap"
    assert result["champion_sharpe"] is None
    assert result["challenger_sharpe"] == pytest.approx(1.5)


def test_gate_passed_challenger_wins():
    """Challenger Sharpe beats champion * 1.05 → deactivate champion, activate challenger."""
    # champion_sharpe=1.0, challenger_sharpe=1.5 → 1.5 > 1.0 * 1.05 ✓
    champion_row = _fake_row(id=10, sharpe="1.0")
    insert_row = _fake_row(id=42, is_active=True)
    result = _call_direct(
        execute_returns=[
            champion_row,  # SELECT champion
            None,          # UPDATE (deactivate old champion)
            insert_row,    # INSERT RETURNING
        ]
    )

    assert result["id"] == 42
    assert result["is_active"] is True
    assert result["gate_outcome"] == "gate_passed"
    assert result["champion_sharpe"] == pytest.approx(1.0)
    assert result["challenger_sharpe"] == pytest.approx(1.5)


def test_gate_held_challenger_loses():
    """Challenger Sharpe does not beat champion * 1.05 → register-only, is_active=False."""
    # champion_sharpe=2.0, challenger_sharpe=2.0 → 2.0 > 2.0 * 1.05 = 2.1 ✗
    champion_row = _fake_row(id=10, sharpe="2.0")
    insert_row = _fake_row(id=42, is_active=False)
    result = _call_direct(
        execute_returns=[
            champion_row,  # SELECT champion
            insert_row,    # INSERT RETURNING (no UPDATE — gate held)
        ]
    )

    assert result["id"] == 42
    assert result["is_active"] is False
    assert result["gate_outcome"] == "gate_held"
    assert result["champion_sharpe"] == pytest.approx(2.0)
    assert result["challenger_sharpe"] == pytest.approx(1.5)


def test_gate_undecidable_champion_no_sharpe():
    """Champion has NULL eval_results->>'sharpe' → gate_undecidable, register-only."""
    champion_row = _fake_row(id=10, sharpe=None)
    insert_row = _fake_row(id=42, is_active=False)
    result = _call_direct(
        execute_returns=[
            champion_row,  # SELECT champion (sharpe=NULL)
            insert_row,    # INSERT RETURNING
        ]
    )

    assert result["id"] == 42
    assert result["is_active"] is False
    assert result["gate_outcome"] == "gate_undecidable"
    assert result["champion_sharpe"] is None


def test_force_bypasses_gate_and_activates():
    """--force=True unconditionally deactivates champion and activates challenger."""
    # champion_sharpe=5.0 (challenger=1.5 would lose the gate normally)
    insert_row = _fake_row(id=42, is_active=True)
    result = _call_direct(
        force=True,
        execute_returns=[
            None,        # UPDATE (deactivate champion) — first call, no meaningful return
            insert_row,  # INSERT RETURNING
        ]
    )

    assert result["id"] == 42
    assert result["is_active"] is True
    assert result["gate_outcome"] == "force"
    # champion_sharpe/challenger_sharpe not queried in force path
    assert result["champion_sharpe"] is None


def test_gate_held_negative_champion_additive_floor():
    """Regression: with negative champion, multiplicative bar inverts and lets worse challengers pass.

    champion=-2.0 → multiplicative threshold = -2.0 * 1.05 = -2.1.
    Without additive floor challenger=-2.05 would satisfy -2.05 > -2.1 (bug).
    With additive floor: max(-2.1, -2.0+0.05=-1.95) = -1.95; -2.05 > -1.95 is False → gate_held.
    """
    worse_challenger_payload = {**_BASE_PAYLOAD, "eval_results": {"sharpe": -2.05}}
    champion_row = _fake_row(id=10, sharpe="-2.0")
    insert_row = _fake_row(id=42, is_active=False)
    result = _call_direct(
        payload=worse_challenger_payload,
        execute_returns=[
            champion_row,  # SELECT champion
            insert_row,    # INSERT RETURNING
        ],
    )

    assert result["gate_outcome"] == "gate_held", (
        "A challenger with Sharpe -2.05 must NOT beat a champion at -2.0 "
        "(challenger is worse in absolute terms). Check additive floor in sharpe_passes()."
    )
    assert result["is_active"] is False


def test_gate_passed_negative_champion_genuinely_better():
    """Challenger that meaningfully beats a negative champion passes the gate."""
    better_challenger_payload = {**_BASE_PAYLOAD, "eval_results": {"sharpe": -1.9}}
    champion_row = _fake_row(id=10, sharpe="-2.0")
    insert_row = _fake_row(id=42, is_active=True)
    result = _call_direct(
        payload=better_challenger_payload,
        execute_returns=[
            champion_row,  # SELECT champion
            None,          # UPDATE (deactivate old champion)
            insert_row,    # INSERT RETURNING
        ],
    )

    # -1.9 > max(-2.1, -1.95) = -1.95 → True (challenger improved by 0.1)
    assert result["gate_outcome"] == "gate_passed"
    assert result["is_active"] is True


def test_cron_register_cmd_no_force():
    """Regression: REGISTER_CMD in hetzner_brain_cron.sh must NOT contain --force."""
    import re

    cron_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "hetzner_brain_cron.sh"
    )
    text = cron_path.read_text(encoding="utf-8")

    m = re.search(r'REGISTER_CMD="([^"]*)"', text, re.DOTALL)
    assert m is not None, "REGISTER_CMD variable not found in hetzner_brain_cron.sh"
    register_cmd = m.group(1)

    assert "--force" not in register_cmd, (
        "REGISTER_CMD must not contain --force — that flag bypasses the Sharpe "
        "gate and unconditionally replaces the champion on every nightly cron run."
    )


def test_cron_has_backend_restart_on_activation():
    """Regression: cron must restart the backend when a checkpoint is activated.

    is_active flips in DB at registration time but the running backend only
    loads the policy at startup — without a restart the promotion is a no-op
    until someone manually restarts the container.
    """
    cron_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "hetzner_brain_cron.sh"
    )
    text = cron_path.read_text(encoding="utf-8")
    assert "docker compose restart backend" in text, (
        "hetzner_brain_cron.sh must call 'docker compose restart backend' after "
        "activation so the newly-promoted checkpoint is loaded into memory. "
        "Without this, every nightly promotion is silently a no-op."
    )
