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

    def fake_news_ingest(_factory: Any) -> Any:
        calls.append("news_ingest")
        return _NoopTask()

    def fake_news_cleanup(_factory: Any) -> Any:
        calls.append("news_cleanup")
        return _NoopTask()

    monkeypatch.setattr(app_main, "start_background_worker", fake_live)
    monkeypatch.setattr(app_main, "start_shadow_worker", fake_shadow)
    monkeypatch.setattr(app_main, "start_news_ingest_task", fake_news_ingest)
    monkeypatch.setattr(app_main, "start_news_cleanup_task", fake_news_cleanup)
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
    """When env is prod-like AND worker_enabled, every background worker spawns."""
    # SP-3 Phase F adds universe_sync + health_pinger background tasks; SP-7
    # Phase D3 adds the audit verifier; SP-9 Phase D4 adds news ingest +
    # cleanup. Stub them all so the lifespan doesn't try to open real DB
    # connections during this isolated startup test.
    class _NoopTask:
        def cancel(self) -> None:
            pass

    monkeypatch.setattr(
        app_main, "start_universe_sync_task", lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_health_pinger_task", lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "start_audit_verifier_task", lambda *_a, **_k: _NoopTask(),
    )
    monkeypatch.setattr(
        app_main, "load_active_checkpoint", _async_noop,
    )
    monkeypatch.setattr(
        app_main, "get_session_factory", lambda: _NoopFactory(),
    )
    monkeypatch.setattr(
        app_main, "get_engine", lambda: type("E", (), {"sync_engine": None})(),
    )
    monkeypatch.setattr(app_main, "attach_query_guard", lambda *_a, **_k: None)

    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {
            "env": "production",
            "worker_enabled": True,
            # SP-8 Phase J: leave the autonomous-trading subsystem off
            # in this startup test so the pre-flight branch doesn't
            # need a vault / Binance key / migration in the SQLite
            # mock environment.
            "autonomous_trading_enabled": False,
            "binance_use_testnet": True,
        })(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert "live" in calls
    assert "shadow" in calls
    assert "news_ingest" in calls
    assert "news_cleanup" in calls


async def _async_noop(*_a: Any, **_k: Any) -> None:
    return None


class _NoopFactory:
    """Async-context-manager-shaped session factory used by the spy test."""

    def __call__(self) -> "_NoopFactory":
        return self

    async def __aenter__(self) -> Any:
        class _S:
            async def commit(self) -> None:
                return None
        return _S()

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_lifespan_skips_news_workers_in_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """News ingest + cleanup must stay off in test/CI exactly like the others."""
    monkeypatch.setattr(
        app_main, "get_settings",
        lambda: type("S", (), {"env": "test", "worker_enabled": True})(),
    )
    with _spy_workers(monkeypatch) as calls:
        app = app_main.create_app()
        await _drive_lifespan(app)

    assert "news_ingest" not in calls
    assert "news_cleanup" not in calls
