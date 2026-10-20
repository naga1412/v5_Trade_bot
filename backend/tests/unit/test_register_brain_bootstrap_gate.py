"""Bootstrap-gate SQL binding and CLI wiring.

The gate DECISION is covered in test_register_brain_sharpe_gate.py,
which owns the canonical harness. This file covers the two things that
file does not: what the decision actually binds into the INSERT, and
whether the CLI reaches the gate with the right default.

Before 2026-10-20 ``_direct_db_register_and_activate``'s no-incumbent
branch activated unconditionally ("bootstrap"). The Sharpe gate only
gates when there is something to compare against, so after #456/#463
deactivated the stale champion, the first successful nightly run
installed whatever it had just trained. That is how a cold-start
random-init policy (id=84, sharpe -17.80) became live champion.

These tests drive the REAL promotion path against a fake session, so
they cover the branch AND the SQL binding it produces -- not a
restatement of a helper the real path might not even call. Each was
mutation-checked by reverting the guard and confirming it goes red.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "tools" / "ml"))

import register_brain  # noqa: E402


class _Row:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Records every execute; answers the champion lookup with `champion`."""

    def __init__(self, champion: Any) -> None:
        self.champion = champion
        self.calls: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, stmt: Any, params: dict | None = None) -> _Result:
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        if sql.startswith("INSERT INTO rl_checkpoints"):
            return _Result(_Row(id=999, is_active=(params or {}).get("a")))
        if "is_active=TRUE" in sql and sql.startswith("SELECT"):
            return _Result(self.champion)
        return _Result(None)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch):
    """Patch get_session_factory; return a factory capturing the session."""
    holder: dict[str, _FakeSession] = {}

    def _install(champion: Any) -> _FakeSession:
        session = _FakeSession(champion)
        holder["s"] = session
        import app.db.session as dbs

        monkeypatch.setattr(dbs, "get_session_factory", lambda: (lambda: session))
        return session

    return _install


_PAYLOAD = {
    "model_name": "ppo_policy_v1",
    "version": "v1-test",
    "checkpoint_uri": "file:///tmp/x.pt",
    "sha256": "deadbeef",
    "trained_at": "2026-10-20T19:51:53+00:00",
    "train_data_window": "365d",
    "eval_results": {"sharpe": -17.8},
    "notes": None,
}


def _insert_params(session: _FakeSession) -> dict:
    for sql, params in session.calls:
        if sql.startswith("INSERT INTO rl_checkpoints"):
            return params
    raise AssertionError("no INSERT was issued")


def test_no_champion_does_not_activate_by_default(fake_db) -> None:
    """THE REGRESSION: zero active rows must register WITHOUT activating."""
    session = fake_db(None)

    result = register_brain._direct_db_register_and_activate(
        payload=dict(_PAYLOAD), activate_flag=True, force=False,
    )

    assert result["gate_outcome"] == "bootstrap_blocked"
    assert result["is_active"] is False, "an unattended run installed a champion"
    assert _insert_params(session)["a"] is False, (
        "the INSERT bound is_active=True despite the gate blocking it"
    )


def test_no_champion_never_deactivates_anything(fake_db) -> None:
    """A blocked bootstrap must not issue the champion-deactivating UPDATE."""
    session = fake_db(None)

    register_brain._direct_db_register_and_activate(
        payload=dict(_PAYLOAD), activate_flag=True, force=False,
    )

    updates = [s for s, _ in session.calls if s.startswith("UPDATE")]
    assert updates == [], f"unexpected UPDATE on a blocked bootstrap: {updates}"


def test_cli_defaults_to_blocking_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Wiring check: the flag must default OFF through argparse, so the
    nightly cron (which passes --activate but NOT the new flag) cannot
    activate. Without this the branch could be correct while the CLI
    silently passed True."""
    seen: dict[str, Any] = {}

    def _spy(**kw: Any) -> dict:
        seen.update(kw)
        return {"id": 1, "version": "v", "is_active": False,
                "gate_outcome": "bootstrap_blocked",
                "champion_sharpe": None, "challenger_sharpe": None}

    monkeypatch.setattr(
        register_brain, "_direct_db_register_and_activate", _spy,
    )
    monkeypatch.setattr(
        register_brain, "build_payload", lambda **kw: dict(_PAYLOAD),
    )

    ckpt = tmp_path / "x.pt"
    ckpt.write_bytes(b"not a real checkpoint")
    ev = tmp_path / "eval.json"
    ev.write_text('{"sharpe": -17.8}')

    rc = register_brain.main([
        "--checkpoint", str(ckpt), "--eval", str(ev),
        "--direct", "--activate",
    ])

    assert rc == 0
    assert seen.get("allow_bootstrap") is False, (
        "argparse must default --allow-bootstrap-activation to False"
    )


def test_cli_opt_in_flag_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """And when explicitly passed, it must actually arrive."""
    seen: dict[str, Any] = {}

    monkeypatch.setattr(
        register_brain, "_direct_db_register_and_activate",
        lambda **kw: (seen.update(kw) or {
            "id": 1, "version": "v", "is_active": True,
            "gate_outcome": "bootstrap",
            "champion_sharpe": None, "challenger_sharpe": None}),
    )
    monkeypatch.setattr(
        register_brain, "build_payload", lambda **kw: dict(_PAYLOAD),
    )

    ckpt = tmp_path / "x.pt"
    ckpt.write_bytes(b"x")
    ev = tmp_path / "eval.json"
    ev.write_text('{"sharpe": -17.8}')

    register_brain.main([
        "--checkpoint", str(ckpt), "--eval", str(ev),
        "--direct", "--activate", "--allow-bootstrap-activation",
    ])

    assert seen.get("allow_bootstrap") is True
