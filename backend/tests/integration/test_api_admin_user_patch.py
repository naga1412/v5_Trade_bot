"""Integration tests for PATCH /api/v1/admin/users/{user_id} (Phase G4)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User


@pytest.mark.asyncio
async def test_patch_user_toggle_active_off(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/users/2", json={"is_active": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None and u.is_active is False


@pytest.mark.asyncio
async def test_patch_user_set_admin_true(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/users/2", json={"is_admin": True},
    )
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_patch_user_cannot_demote_self(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/users/1", json={"is_admin": False},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_user_set_notes(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/users/2", json={"notes": "Trusted friend"},
    )
    assert r.status_code == 200
    async with auth_factory() as s:
        row = (
            await s.execute(sa.select(User.notes).where(User.id == 2))
        ).scalar_one()
        assert row == "Trusted friend"


@pytest.mark.asyncio
async def test_patch_user_404_when_unknown(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.patch(
        "/api/v1/admin/users/9999", json={"is_active": False},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_user_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.patch(
        "/api/v1/admin/users/3", json={"is_active": True},
    )
    assert r.status_code == 403
