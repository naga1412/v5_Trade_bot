"""Tests for impersonation_state CRUD + audit event logging."""

import pytest
import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.impersonation import (
    clear_active_target,
    get_active_target,
    log_event,
    set_active_target,
)
from app.auth.models import Base, ImpersonationEvent, User


def _fake_request() -> Request:
    """Minimal Request stub for tests that don't exercise URL paths."""
    scope: dict = {
        "type": "http",
        "headers": [],
        "method": "GET",
        "path": "/x",
        "query_string": b"",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_set_get_clear_impersonation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            sa.text(
                "CREATE TABLE impersonation_state ("
                "admin_user_id INTEGER PRIMARY KEY, "
                "target_user_id INTEGER NOT NULL, "
                "started_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        admin = User(email="a@x.com", display_name="A", is_admin=True)
        target = User(email="b@x.com", display_name="B")
        session.add_all([admin, target])
        await session.commit()

        await set_active_target(
            session, admin_id=admin.id, target_id=target.id,
        )
        await session.commit()

        req = _fake_request()
        active = await get_active_target(req, admin=admin, _session=session)
        assert active == target.id

        await clear_active_target(session, admin_id=admin.id)
        await session.commit()

        active2 = await get_active_target(req, admin=admin, _session=session)
        assert active2 is None


@pytest.mark.asyncio
async def test_set_active_target_upserts() -> None:
    """Calling set twice for the same admin updates target, doesn't duplicate."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            sa.text(
                "CREATE TABLE impersonation_state ("
                "admin_user_id INTEGER PRIMARY KEY, "
                "target_user_id INTEGER NOT NULL, "
                "started_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        admin = User(email="a@x.com", display_name="A", is_admin=True)
        t1 = User(email="t1@x.com", display_name="T1")
        t2 = User(email="t2@x.com", display_name="T2")
        session.add_all([admin, t1, t2])
        await session.commit()

        await set_active_target(session, admin_id=admin.id, target_id=t1.id)
        await session.commit()
        await set_active_target(session, admin_id=admin.id, target_id=t2.id)
        await session.commit()

        rows = (
            await session.execute(sa.text("SELECT * FROM impersonation_state"))
        ).all()
    assert len(rows) == 1
    req = _fake_request()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        active = await get_active_target(req, admin=admin, _session=session)
    assert active == t2.id


@pytest.mark.asyncio
async def test_log_event_creates_impersonation_event_row() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        admin = User(email="a@x.com", display_name="A", is_admin=True)
        target = User(email="b@x.com", display_name="B")
        session.add_all([admin, target])
        await session.commit()

        await log_event(
            session,
            admin_user_id=admin.id,
            target_user_id=target.id,
            action="start",
            request_path="/api/v1/admin/impersonate/2",
        )
        await session.commit()

        rows = (
            await session.execute(sa.select(ImpersonationEvent))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "start"
    assert rows[0].request_path == "/api/v1/admin/impersonate/2"
