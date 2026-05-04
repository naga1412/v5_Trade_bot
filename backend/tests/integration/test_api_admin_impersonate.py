"""Integration tests for POST /api/v1/admin/impersonate/{user_id} (G6)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import ImpersonationEvent, User


@pytest.mark.asyncio
async def test_impersonate_start_writes_state_and_event(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await admin_client.post("/api/v1/admin/impersonate/2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin_user_id"] == 1
    assert body["target_user_id"] == 2
    async with auth_factory() as s:
        rows = (await s.execute(sa.text(
            "SELECT admin_user_id, target_user_id FROM impersonation_state"
        ))).all()
        events = (await s.execute(sa.select(ImpersonationEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].admin_user_id == 1
    assert rows[0].target_user_id == 2
    assert any(e.action == "start" for e in events)


@pytest.mark.asyncio
async def test_impersonate_self_400(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.post("/api/v1/admin/impersonate/1")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_impersonate_other_admin_400(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None
        u.is_admin = True
        await s.commit()
    r = await admin_client.post("/api/v1/admin/impersonate/2")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_impersonate_inactive_target_400(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.post("/api/v1/admin/impersonate/3")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_impersonate_unknown_target_404(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.post("/api/v1/admin/impersonate/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_impersonate_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.post("/api/v1/admin/impersonate/3")
    assert r.status_code == 403
