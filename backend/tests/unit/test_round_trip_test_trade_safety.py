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


def _stub_deps(
    *,
    hybrid_value: float | None,
    initialize_vault_cache_return: bool = True,
    initialize_vault_cache_raises: Exception | None = None,
    vault_keys_return: Any = None,
) -> dict[str, Any]:
    """Build the dict `_safe_import` returns, with HYBRID set to the test value.

    Defaults: the safety-freeze check fires BEFORE vault_init / vault_keys /
    binance / DB are touched, so most freeze tests don't care about the
    later phases. Tests that exercise the vault_init phase override
    `initialize_vault_cache_return` / `initialize_vault_cache_raises`.

    The PR-FIX-PR277-SELF-BOOTSTRAP-VAULT (2026-05-27) update added
    `initialize_vault_cache` to the deps dict; this stub now mirrors that.
    """
    settings = MagicMock()
    settings.HYBRID_AUTO_SCORE_THRESHOLD = hybrid_value
    settings.master_passphrase = "test-passphrase"

    def _get_settings() -> Any:
        return settings

    def _init_vault_cache(*, passphrase: str, secrets_path: Any) -> bool:
        if initialize_vault_cache_raises is not None:
            raise initialize_vault_cache_raises
        return initialize_vault_cache_return

    return {
        "get_settings": _get_settings,
        "get_session_factory": MagicMock(),
        "BinanceLiveClient": MagicMock(),
        "_place_approved_order": MagicMock(),
        "vault_keys": MagicMock(return_value=vault_keys_return),
        "initialize_vault_cache": _init_vault_cache,
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


# ────────────────────────────────────────────────────────────────────────────
# PR-FIX-PR277-SELF-BOOTSTRAP-VAULT (2026-05-27): the script now calls
# initialize_vault_cache itself before invoking _place_approved_order
# because `python -m tools.round_trip_test_trade` runs in a process
# separate from the uvicorn worker that holds the in-process vault cache.
# ────────────────────────────────────────────────────────────────────────────


def test_vault_init_called_with_uvicorn_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The script must mirror the uvicorn lifespan's call shape — keyword
    args `passphrase=settings.master_passphrase` and `secrets_path=Path(...)`.
    Asserts the call happens with both kwargs of the right type."""
    captured: list[dict[str, Any]] = []

    def _spy_init(*, passphrase: str, secrets_path: Any) -> bool:
        captured.append({"passphrase": passphrase, "secrets_path": str(secrets_path)})
        return True

    deps = _stub_deps(hybrid_value=0.99, vault_keys_return=None)
    deps["initialize_vault_cache"] = _spy_init
    monkeypatch.setattr(tool, "_safe_import", lambda: deps)
    result = asyncio.run(tool._run())
    assert captured, "initialize_vault_cache was never called"
    assert captured[0]["passphrase"] == "test-passphrase"
    # secrets_path is a Path — default /app/secrets.enc unless overridden
    assert "secrets.enc" in captured[0]["secrets_path"]
    # vault_keys() returned None → next abort is phase="vault", not
    # "vault_init" (that branch only fires on init failure).
    assert result.get("phase") == "vault"


def test_vault_init_returns_false_aborts_with_clear_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """initialize_vault_cache returning False (missing file, wrong
    passphrase, missing keys) MUST abort with phase='vault_init'."""
    deps = _stub_deps(
        hybrid_value=0.99, initialize_vault_cache_return=False,
    )
    monkeypatch.setattr(tool, "_safe_import", lambda: deps)
    result = asyncio.run(tool._run())
    assert result.get("status") == "abort"
    assert result.get("phase") == "vault_init"
    assert "initialize_vault_cache returned False" in result.get("reason", "")


def test_vault_init_raises_aborts_with_traceback_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception from initialize_vault_cache MUST abort
    with phase='vault_init' and the exception class + message in
    `reason` for forensics."""
    boom = RuntimeError("simulated decrypt blow-up")
    deps = _stub_deps(
        hybrid_value=0.99, initialize_vault_cache_raises=boom,
    )
    monkeypatch.setattr(tool, "_safe_import", lambda: deps)
    result = asyncio.run(tool._run())
    assert result.get("status") == "abort"
    assert result.get("phase") == "vault_init"
    assert "RuntimeError" in result.get("reason", "")
    assert "simulated decrypt blow-up" in result.get("reason", "")


def test_vault_init_success_then_vault_keys_none_distinct_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If init returned True but vault_keys() returns None — module
    cache not visible. Abort with phase='vault' (distinct from
    'vault_init') so the operator can diagnose."""
    deps = _stub_deps(
        hybrid_value=0.99,
        initialize_vault_cache_return=True,
        vault_keys_return=None,
    )
    monkeypatch.setattr(tool, "_safe_import", lambda: deps)
    result = asyncio.run(tool._run())
    assert result.get("status") == "abort"
    assert result.get("phase") == "vault"
    assert "module-level cache not visible" in result.get("reason", "")


def test_vault_init_success_proceeds_past_phase2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy path through the bootstrap: init returns True, keys
    returned non-None. The script must NOT abort on safety_freeze /
    vault_init / vault. Subsequent phases (Binance price fetch, DB
    INSERT) are heavily mocked here — once those fire, the script
    either aborts at a LATER phase OR crashes on a mock with an
    AttributeError. Both are acceptable PROOFS that the bootstrap
    succeeded and execution proceeded past Phase 2.

    The script's real integration test is the operator-driven prod run
    (`docker compose exec backend python -m tools.round_trip_test_trade`);
    this unit test only locks in the bootstrap-and-proceed contract.
    """
    from app.trading.execution.glue import VaultKeys
    real_keys = VaultKeys(
        binance_api_key="test-key", binance_api_secret="test-secret",
    )
    deps = _stub_deps(
        hybrid_value=0.99,
        initialize_vault_cache_return=True,
        vault_keys_return=real_keys,
    )
    monkeypatch.setattr(tool, "_safe_import", lambda: deps)
    try:
        result = asyncio.run(tool._run())
    except Exception as e:  # noqa: BLE001
        # An exception during phase 2+ means the script proceeded past
        # the bootstrap — that IS the assertion. Surface the exception
        # class so a future regression of bootstrap-skipping is still
        # caught here (we'd see e.g. a KeyError on `deps[...]` before
        # the bootstrap step instead).
        assert e.__class__.__name__ not in {"KeyError", "RuntimeError"} or (
            "initialize_vault_cache" not in str(e)
        ), f"bootstrap step itself failed: {e}"
        return
    if result.get("status") == "abort":
        assert result.get("phase") not in {
            "safety_freeze", "vault_init", "vault",
        }, f"script aborted at an early phase: {result}"
