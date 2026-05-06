"""Integration tests for POST/GET /api/v1/admin/hyperopt — SP-7 Phase C4.

Mirrors the SP-7 Phase B5 admin-routes test pattern: ``admin_client``
(acting as user_id=1, admin) plus ``friend_client`` (non-admin) for the
403 case. The ``hyperopt_studies`` table is created by the auth conftest
(see ``_create_auth_tables``).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def admin_client_with_bg(
    auth_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """Admin client that also overrides the background-task session factory
    so the BackgroundTasks dispatched by POST /api/v1/admin/hyperopt write
    to the same in-memory SQLite engine the request session reads from.
    """
    from app.api.routes.admin_hyperopt import get_background_session_factory
    from app.auth.deps import (
        current_user_or_impersonated,
        require_admin,
        require_user,
    )
    from app.auth.models import User
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    user = User(
        id=1, email="admin@x.com", display_name="admin",
        is_admin=True, is_active=True,
        trading_mode="manual", position_sizing_mode="fixed",
        quiet_hours_enabled=True,
    )

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="admin@x.com", sub="admin", raw={})

    async def _u() -> Any:
        return user

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with auth_factory() as session:
            yield session

    def _override_bg_factory() -> async_sessionmaker[AsyncSession]:
        return auth_factory

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[require_admin] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u
    app.dependency_overrides[get_background_session_factory] = _override_bg_factory
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            require_admin, current_user_or_impersonated,
            get_background_session_factory,
        ):
            app.dependency_overrides.pop(d, None)


@pytest.mark.asyncio
async def test_post_hyperopt_admin_only(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Non-admin user gets 403 from POST."""
    resp = await friend_client.post("/api/v1/admin/hyperopt", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 5,
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_hyperopt_admin_only(friend_client) -> None:  # type: ignore[no-untyped-def]
    """Non-admin user gets 403 from GET."""
    resp = await friend_client.get("/api/v1/admin/hyperopt/1")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_hyperopt_returns_202_with_running_status(  # type: ignore[no-untyped-def]
    admin_client_with_bg, monkeypatch,
) -> None:
    """Admin POST returns 202 + a 'running' or 'completed' row immediately."""
    from app.api.routes import admin_hyperopt as ah_route
    from tools.hyperopt import HyperoptResult

    def _fast_runner(**kw: Any) -> HyperoptResult:
        # Bypass Optuna entirely — return a canned result.
        return HyperoptResult(
            best_weights={i: 1 / 9 for i in range(1, 10)},
            best_sharpe=0.5, n_trials=int(kw.get("n_trials", 5)),
            study=None,
        )

    monkeypatch.setattr(ah_route, "hyperopt_layer_weights", _fast_runner)

    resp = await admin_client_with_bg.post("/api/v1/admin/hyperopt", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 5,
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] in ("running", "completed")  # may have raced
    new_id = body["id"]

    # Poll the GET endpoint.
    follow = await admin_client_with_bg.get(
        f"/api/v1/admin/hyperopt/{new_id}",
    )
    assert follow.status_code == 200
    assert follow.json()["id"] == new_id


@pytest.mark.asyncio
async def test_get_hyperopt_404_on_missing(admin_client) -> None:  # type: ignore[no-untyped-def]
    """GET on a non-existent id returns 404."""
    resp = await admin_client.get("/api/v1/admin/hyperopt/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_hyperopt_caps_n_trials(admin_client) -> None:  # type: ignore[no-untyped-def]
    """n_trials > 500 returns 400 (capped to prevent runaway)."""
    resp = await admin_client.post("/api/v1/admin/hyperopt", json={
        "symbol": "BTC/USDT", "timeframe": "1h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 999,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_hyperopt_returns_seeded_rows(  # type: ignore[no-untyped-def]
    admin_client_with_bg, monkeypatch,
) -> None:
    """GET /api/v1/admin/hyperopt returns past studies, newest-first."""
    from app.api.routes import admin_hyperopt as ah_route
    from tools.hyperopt import HyperoptResult

    def _fast_runner(**kw: Any) -> HyperoptResult:
        return HyperoptResult(
            best_weights={i: 1 / 9 for i in range(1, 10)},
            best_sharpe=0.5, n_trials=int(kw.get("n_trials", 5)),
            study=None,
        )

    monkeypatch.setattr(ah_route, "hyperopt_layer_weights", _fast_runner)

    # Seed one row.
    seed = await admin_client_with_bg.post("/api/v1/admin/hyperopt", json={
        "symbol": "ETH/USDT", "timeframe": "4h",
        "train_start": "2025-01-01T00:00:00Z",
        "train_end":   "2025-06-30T00:00:00Z",
        "val_start":   "2025-07-01T00:00:00Z",
        "val_end":     "2025-12-31T00:00:00Z",
        "n_trials": 3,
    })
    assert seed.status_code == 202

    listing = await admin_client_with_bg.get(
        "/api/v1/admin/hyperopt?limit=10",
    )
    assert listing.status_code == 200
    items = listing.json()
    assert isinstance(items, list)
    assert any(it["symbol"] == "ETH/USDT" for it in items)
