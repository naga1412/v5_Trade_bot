"""Integration tests for POST /api/v1/admin/invitations (SP-0.7 Phase G3)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import PendingInvitation


@pytest.mark.asyncio
async def test_post_admin_invitations_creates_row(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    body = {
        "email": "newbie@x.com",
        "display_name": "Newbie",
        "is_admin": False,
        "notes": "from admin",
    }
    r = await admin_client.post("/api/v1/admin/invitations", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["email"] == "newbie@x.com"
    assert out["display_name"] == "Newbie"
    assert out["invited_by"] == 1
    assert out["accepted_at"] is None
    assert out["cf_access_added"] is False

    async with auth_factory() as session:
        rows = (
            await session.execute(
                sa.select(PendingInvitation).where(
                    PendingInvitation.email == "newbie@x.com"
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_post_admin_invitations_409_when_email_exists_in_users(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.post(
        "/api/v1/admin/invitations",
        json={"email": "friend@x.com", "display_name": "Dup"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_post_admin_invitations_409_when_already_invited(
    admin_client: httpx.AsyncClient,
) -> None:
    r1 = await admin_client.post(
        "/api/v1/admin/invitations",
        json={"email": "twice@x.com", "display_name": "Once"},
    )
    assert r1.status_code == 201
    r2 = await admin_client.post(
        "/api/v1/admin/invitations",
        json={"email": "twice@x.com", "display_name": "Twice"},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_post_admin_invitations_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.post(
        "/api/v1/admin/invitations",
        json={"email": "x@x.com", "display_name": "X"},
    )
    assert r.status_code == 403
