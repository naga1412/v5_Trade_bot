"""PR-FIX-FLAG-BINDING: env→Settings binding contract for strategy flags.

Background
----------
2026-05-21: the operator manually set DISABLE_SHORT_SIGNALS=true in
/opt/trading-radar/.env and restarted the backend container.  5h later,
the verify-after-soak probe (run 26249116921) found
``get_settings().DISABLE_SHORT_SIGNALS == False`` despite the .env
contents.  Root cause: backend service in ``docker-compose.yml`` had no
``env_file: .env`` directive, so .env values never reached the container
process environment; pydantic-settings's ``env_file=".env"`` fallback
also failed because the .env file isn't bind-mounted into the container.

These tests defend the contract on the application side:

  1. Each strategy flag in the operator-tunable set is declared on the
     ``Settings`` class.  If a future PR ships a gate that depends on a
     new flag but forgets the ``Settings`` field, ``model_fields`` won't
     have it and this test fails loudly.

  2. ``Settings`` reads each tunable flag from the process env when the
     env var is set.  pydantic-settings already supports this — the test
     proves it stays working under refactors (model_config changes,
     env_prefix introductions, case_sensitive flips, …).

The docker-compose half of the fix is covered separately in
``test_docker_compose_env_file_present.py``.
"""
from __future__ import annotations

import pytest

from app.config import Settings


# Operator-tunable strategy + launch flags. Each one was at least once
# "shipped default-OFF, operator flips per env after staging soak" —
# meaning the env→Settings binding is load-bearing for the rollout plan.
# Same failure mode that hid DISABLE_SHORT_SIGNALS would apply to any of
# these if the env_file or Settings declaration regresses.
_OPERATOR_TUNABLE_FLAGS: frozenset[str] = frozenset({
    # SP-8 Phase J: autonomous-launch master switches
    "autonomous_trading_enabled",
    "auto_promote_to_telegram_enabled",
    "auto_promote_to_fullyauto_enabled",
    "worker_enabled",
    # PR-strategy-1 (2026-05-20)
    "DISABLE_SHORT_SIGNALS",
    "MIN_ENTRY_SCORE_LONG",
    # PR10 (2026-05-20)
    "SYMBOL_ALLOWLIST_ENABLED",
    # PR9 (2026-05-18)
    "DYNAMIC_SIZING_ENABLED",
    # PR8 (2026-05-18)
    "LIVE_COOLDOWN_ENABLED",
    # PR3 G1 (2026-05-18)
    "HOLD_TP_SCALING_ENABLED",
    # PR3 (2026-05-18)
    "SHADOW_TIMEFRAMES",
    "SHADOW_15M_ELIGIBLE_FOR_PROMOTION",
    # PR2 (2026-05-14)
    "MTF_MIN_AGREEMENT_1H",
    # PR10.5 / FU-28 (2026-05-20)
    "FU28_AUTO_RECYCLE_ENABLED",
})


def _s(**kwargs) -> Settings:
    # ``_env_file=None`` immunises the test against a repo-root `.env`
    # contaminating Settings construction (Settings.model_config has
    # ``env_file=".env"`` at app/config.py — pydantic-settings reads it
    # by default).  Tests assert behavior under explicit env state only.
    return Settings(
        database_url="postgresql://x", redis_url="redis://x",
        _env_file=None, **kwargs,
    )


@pytest.fixture(autouse=True)
def _isolate_strategy_flag_env(monkeypatch) -> None:
    """Strip operator-tunable flags from the shell env before every test.

    A developer running ``DISABLE_SHORT_SIGNALS=true pytest`` (or with the
    same line in a non-test shell rc) would otherwise see false failures.
    """
    for flag in _OPERATOR_TUNABLE_FLAGS:
        monkeypatch.delenv(flag, raising=False)
        # case_sensitive=False on Settings means the lowercase form binds
        # too — purge it as well.
        monkeypatch.delenv(flag.upper(), raising=False)
        monkeypatch.delenv(flag.lower(), raising=False)


def test_all_strategy_flags_present_in_settings() -> None:
    """Every operator-tunable strategy flag must be declared on Settings.

    Without the declaration, pydantic-settings cannot bind the env var
    and the flag silently uses its hard-coded default — the exact class
    of bug that hid DISABLE_SHORT_SIGNALS for 5 days.
    """
    missing = _OPERATOR_TUNABLE_FLAGS - set(Settings.model_fields.keys())
    assert not missing, (
        f"Strategy flags declared in Settings: missing {missing}. "
        "If a flag was renamed, update _OPERATOR_TUNABLE_FLAGS. "
        "If a new flag was added, add it here AND to the docker-compose "
        "env_file path (see PR-FIX-FLAG-BINDING)."
    )


def test_settings_reads_disable_short_signals_from_env(monkeypatch) -> None:
    """The specific flag that hid for 5 days.  Sentinel against regression."""
    monkeypatch.setenv("DISABLE_SHORT_SIGNALS", "true")
    assert _s().DISABLE_SHORT_SIGNALS is True


def test_settings_reads_disable_short_signals_default_false() -> None:
    """Default-OFF semantic.  Unsetting env reverts to safe behavior."""
    # No monkeypatch.setenv — default holds.
    assert _s().DISABLE_SHORT_SIGNALS is False


def test_settings_reads_min_entry_score_long_from_env(monkeypatch) -> None:
    """PR-strategy-1's other flag.  Float | None — must parse both."""
    monkeypatch.setenv("MIN_ENTRY_SCORE_LONG", "0.4")
    assert _s().MIN_ENTRY_SCORE_LONG == pytest.approx(0.4)


def test_settings_min_entry_score_long_default_none() -> None:
    """Default = None disables the gate (gate code reads `is not None`)."""
    assert _s().MIN_ENTRY_SCORE_LONG is None


@pytest.mark.parametrize(
    "flag_name,env_value,expected",
    [
        ("SYMBOL_ALLOWLIST_ENABLED", "true", True),
        ("DYNAMIC_SIZING_ENABLED", "true", True),
        ("LIVE_COOLDOWN_ENABLED", "true", True),
        ("HOLD_TP_SCALING_ENABLED", "true", True),
        ("SHADOW_15M_ELIGIBLE_FOR_PROMOTION", "true", True),
        ("FU28_AUTO_RECYCLE_ENABLED", "true", True),
    ],
)
def test_settings_reads_boolean_strategy_flags_from_env(
    monkeypatch, flag_name: str, env_value: str, expected: bool,
) -> None:
    """All operator-tunable bool flags read their env override.

    Catches the case where a flag is declared on Settings but a future
    refactor breaks the env binding (env_prefix, case_sensitive, …).
    """
    monkeypatch.setenv(flag_name, env_value)
    assert getattr(_s(), flag_name) is expected
