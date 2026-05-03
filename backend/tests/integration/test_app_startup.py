"""Lifespan tests — verify the live + shadow workers stay off in test/CI.

The FastAPI lifespan context manager is the gate that decides whether each
background worker fires. We exercise the manager directly (bypassing httpx,
which doesn't drive ASGI lifespan events) and spy on the worker factories.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from app import main as app_main


@contextmanager
def _spy_workers(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace the worker factories with no-op spies that record each call."""
    calls: list[str] = []

    class _NoopTask:
        def cancel(self) -> None:
            pass

    def fake_live() -> Any:
        calls.append("live")
        return _NoopTask()

    def fake_shadow() -> Any:
        calls.append("shadow")
        return _NoopTask()

    monkeypatch.setattr(app_main, "start_background_worker", fake_live)
    monkeypatch.setattr(app_main, "start_shadow_worker", fake_shadow)
    yield calls


async def _drive_lifespan(app: Any) -> None:
    """Enter and exit the app's lifespan exactly once."""
    async with app_main.lifespan(app):
        pass


@pytest.mark.asyncio
async def test_lifespan_skips_workers_in_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENV=test must skip both the live-prediction and shadow workers."""
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "test", "worker_enabled": True})(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert calls == [], f"Workers must not start in test env, got: {calls}"


@pytest.mark.asyncio
async def test_lifespan_skips_workers_in_ci_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "ci", "worker_enabled": True})(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_skips_workers_when_worker_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "production", "worker_enabled": False})(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_starts_both_workers_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When env is prod-like AND worker_enabled, both workers must spawn."""
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "production", "worker_enabled": True})(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert sorted(calls) == ["live", "shadow"]
