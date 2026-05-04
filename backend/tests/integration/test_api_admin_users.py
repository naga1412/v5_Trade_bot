"""Integration tests for GET /api/v1/admin/users (SP-0.7 Phase G2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.deps import current_user_or_impersonated, require_admin, require_user
from app.auth.models import Base, User
from app.db.session import get_session
from app.deps import CFAccessUser, require_cf_user
from app.main import app


async def _seed_users(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS impersonation_state ("
                "admin_user_id INTEGER PRIMARY KEY, "
                "target_user_id INTEGER NOT NULL, "
                "started_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
        )

    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        admin = User(
            id=1, email="admin@x.com", display_name="Admin",
            is_admin=True, is_active=True,
        )
        friend = User(
            id=2, email="friend@x.com", display_name="Friend",
            is_admin=False, is_active=True,
        )
        deact = User(
            id=3, email="deact@x.com", display_name="Deact",
            is_admin=False, is_active=False,
        )
        session.add_all([admin, friend, deact])
        await session.commit()


@pytest_asyncio.fixture
async def admin_engine() -> AsyncIterator[Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await _seed_users(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def admin_factory(admin_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(admin_engine, expire_on_commit=False)


def _override_session(factory: async_sessionmaker[AsyncSession]) -> Any:
    async def _gen() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s
    return _gen


def _detached(uid: int, email: str, *, is_admin: bool, is_active: bool = True) -> User:
    return User(
        id=uid, email=email, display_name=email.split("@")[0],
        is_admin=is_admin, is_active=is_active,
    )


@pytest_asyncio.fixture
async def admin_client_as_admin(
    admin_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client acting as user_id=1 (admin)."""
    user = _detached(1, "admin@x.com", is_admin=True)

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="admin@x.com", sub="admin", raw={})

    async def _u() -> User:
        return user

    app.dependency_overrides[get_session] = _override_session(admin_factory)
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[require_admin] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            require_admin, current_user_or_impersonated,
        ):
            app.dependency_overrides.pop(d, None)


@pytest_asyncio.fixture
async def admin_client_as_friend(
    admin_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client acting as user_id=2 (non-admin)."""
    user = _detached(2, "friend@x.com", is_admin=False)

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email="friend@x.com", sub="friend", raw={})

    async def _u() -> User:
        return user

    # Only override require_user/cf — keep require_admin's real implementation
    # so non-admin guard fires.
    app.dependency_overrides[get_session] = _override_session(admin_factory)
    app.dependency_overrides[require_cf_user] = _cf
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[current_user_or_impersonated] = _u
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for d in (
            get_session, require_cf_user, require_user,
            current_user_or_impersonated,
        ):
            app.dependency_overrides.pop(d, None)


@pytest.mark.asyncio
async def test_get_admin_users_returns_all_sorted_created_at(
    admin_client_as_admin: httpx.AsyncClient,
) -> None:
    r = await admin_client_as_admin.get("/api/v1/admin/users")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 3
    emails = [u["email"] for u in body]
    assert emails == ["admin@x.com", "friend@x.com", "deact@x.com"]
    # is_active=False users included
    deact = next(u for u in body if u["email"] == "deact@x.com")
    assert deact["is_active"] is False


@pytest.mark.asyncio
async def test_get_admin_users_returns_403_for_non_admin(
    admin_client_as_friend: httpx.AsyncClient,
) -> None:
    r = await admin_client_as_friend.get("/api/v1/admin/users")
    assert r.status_code == 403
