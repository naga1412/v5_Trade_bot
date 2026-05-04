"""Integration tests for GET/PATCH /api/v1/me (Phase H1)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import User


@pytest.mark.asyncio
async def test_get_me_returns_self_shape(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "friend@x.com"
    assert body["is_admin"] is False
    assert body["is_impersonating"] is False
    assert body["binance_keys_configured"] is False
    assert body["telegram_configured"] is False
    assert body["totp_configured"] is False


@pytest.mark.asyncio
async def test_get_me_admin_is_admin_true(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.get("/api/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["is_admin"] is True


@pytest.mark.asyncio
async def test_patch_me_updates_display_name(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me", json={"display_name": "New Friend"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "New Friend"
    async with auth_factory() as s:
        row = (
            await s.execute(sa.select(User.display_name).where(User.id == 2))
        ).scalar_one()
    assert row == "New Friend"


@pytest.mark.asyncio
async def test_patch_me_updates_position_sizing(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r = await friend_client.patch(
        "/api/v1/me",
        json={
            "fixed_size_min_usdt": 30.0,
            "fixed_size_max_usdt": 100.0,
            "max_concurrent_positions": 8,
            "max_leverage_cap": 5,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fixed_size_min_usdt"] == 30.0
    assert body["fixed_size_max_usdt"] == 100.0
    assert body["max_concurrent_positions"] == 8
    assert body["max_leverage_cap"] == 5


@pytest.mark.asyncio
async def test_patch_me_ignores_email_and_admin_fields(
    friend_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    """Body fields email/is_admin are not in MePatchIn -> Pydantic drops them."""
    r = await friend_client.patch(
        "/api/v1/me",
        json={"email": "evil@x.com", "is_admin": True, "display_name": "Y"},
    )
    assert r.status_code == 200
    async with auth_factory() as s:
        u = await s.get(User, 2)
        assert u is not None
        assert u.email == "friend@x.com"
        assert u.is_admin is False
