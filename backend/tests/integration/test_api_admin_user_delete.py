"""Integration tests for DELETE /api/v1/admin/users/{user_id} (Phase G5)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User


@pytest.mark.asyncio
async def test_delete_user_soft_deletes(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await admin_client.delete("/api/v1/admin/users/2")
    assert r.status_code == 204
    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None  # row still present
        assert u.is_active is False


@pytest.mark.asyncio
async def test_delete_user_cannot_delete_self(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.delete("/api/v1/admin/users/1")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_user_404_when_unknown(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.delete("/api/v1/admin/users/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.delete("/api/v1/admin/users/3")
    assert r.status_code == 403
