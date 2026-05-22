"""PR-FIX-FLAG-BINDING: docker-compose must inject host .env into backend.

Catches the actual root cause of the 2026-05-21 DISABLE_SHORT_SIGNALS
regression (verify probe run 26249116921): the backend service in
``docker-compose.yml`` had no ``env_file:`` directive, so .env values
never reached the container process environment regardless of what
pydantic-settings was doing.

A test on Settings alone (see test_settings_env_binding.py) wouldn't
have caught this — Settings.DISABLE_SHORT_SIGNALS reads os.environ
fine in unit tests because pytest's environment IS the process env.
The bug surfaces only at the docker-compose container-boundary layer,
where ``env_file:`` is the contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up from this file until we find docker-compose.yml at root."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "docker-compose.yml").exists():
            return ancestor
    raise RuntimeError(
        "docker-compose.yml not found above " f"{here} — repo layout changed?"
    )


def _load_docker_compose() -> dict:
    yaml = pytest.importorskip("yaml")
    compose_path = _repo_root() / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def test_backend_service_has_env_file_directive() -> None:
    """The fix: ``env_file: - .env`` on backend service.

    Without this, the only env vars the container sees are the ones
    listed in the explicit ``environment:`` allowlist — and operator-
    tunable strategy flags (DISABLE_SHORT_SIGNALS, MIN_ENTRY_SCORE_LONG,
    SYMBOL_ALLOWLIST_ENABLED, …) are NOT in that allowlist by design.
    """
    compose = _load_docker_compose()
    backend = compose["services"]["backend"]
    assert "env_file" in backend, (
        "Backend service missing `env_file:` directive. "
        "Without it, operator-tunable strategy flags in .env are invisible "
        "to the container — see PR-FIX-FLAG-BINDING."
    )
    env_file = backend["env_file"]
    # Accept either form: `env_file: .env` (string) or `env_file: [.env]`
    # (list).  Both forms inject the file.
    files = [env_file] if isinstance(env_file, str) else list(env_file)
    assert ".env" in files, (
        f"Backend env_file must include `.env`, got {files!r}."
    )
