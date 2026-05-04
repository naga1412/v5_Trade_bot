"""Integration tests for DELETE /api/v1/admin/impersonate (G7)."""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import ImpersonationEvent


@pytest.mark.asyncio
async def test_impersonate_stop_clears_state_and_logs_stop(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker,
) -> None:
    r1 = await admin_client.post("/api/v1/admin/impersonate/2")
    assert r1.status_code == 200
    r2 = await admin_client.delete("/api/v1/admin/impersonate")
    assert r2.status_code == 204
    async with auth_factory() as s:
        rows = (
            await s.execute(sa.text("SELECT * FROM impersonation_state"))
        ).all()
        events = (
            await s.execute(sa.select(ImpersonationEvent))
        ).scalars().all()
    assert len(rows) == 0
    assert any(e.action == "stop" for e in events)


@pytest.mark.asyncio
async def test_impersonate_stop_idempotent_when_no_state(
    admin_client: httpx.AsyncClient,
) -> None:
    """DELETE without prior start should still 204 (idempotent)."""
    r = await admin_client.delete("/api/v1/admin/impersonate")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_impersonate_stop_403_for_non_admin(
    friend_client: httpx.AsyncClient,
) -> None:
    r = await friend_client.delete("/api/v1/admin/impersonate")
    assert r.status_code == 403
