"""Integration tests for GET /api/v1/admin/users (SP-0.7 Phase G2)."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_get_admin_users_returns_all_sorted_created_at(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.get("/api/v1/admin/users")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 3
    emails = [u["email"] for u in body]
    assert emails == ["admin@x.com", "friend@x.com", "deact@x.com"]
    deact = next(u for u in body if u["email"] == "deact@x.com")
    assert deact["is_active"] is False


@pytest.mark.asyncio
async def test_get_admin_users_returns_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.get("/api/v1/admin/users")
    assert r.status_code == 403
