"""Failing tests for require_user / require_admin (D5 red)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.deps import require_admin, require_user
from app.auth.models import Base, User
from app.db.session import get_session
from app.deps import CFAccessUser, require_cf_user


@pytest_asyncio.fixture
async def app_with_db() -> AsyncIterator[
    tuple[FastAPI, async_sessionmaker, AsyncEngine]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    app = FastAPI()

    async def _session() -> AsyncIterator:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    yield app, factory, engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_require_user_creates_bootstrap_admin_on_first_login(
    app_with_db: tuple[FastAPI, async_sessionmaker, AsyncEngine],
) -> None:
    app, _, _ = app_with_db

    @app.get("/whoami")
    async def whoami(user: User = Depends(require_user)) -> dict:
        return {"id": user.id, "email": user.email, "is_admin": user.is_admin}

    async def _cf_user_fake() -> CFAccessUser:
        return CFAccessUser(
            email="first@example.com", sub="goog-1", raw={"name": "First"},
        )

    app.dependency_overrides[require_cf_user] = _cf_user_fake

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "first@example.com"
    assert body["is_admin"] is True


@pytest.mark.asyncio
async def test_require_user_403_on_uninvited(
    app_with_db: tuple[FastAPI, async_sessionmaker, AsyncEngine],
) -> None:
    app, factory, _ = app_with_db

    # Pre-populate so users table is non-empty.
    async with factory() as s:
        s.add(User(email="admin@example.com", display_name="A", is_admin=True))
        await s.commit()

    @app.get("/whoami")
    async def whoami(user: User = Depends(require_user)) -> dict:
        return {"id": user.id}

    async def _cf_user_fake() -> CFAccessUser:
        return CFAccessUser(email="hacker@evil.com", sub="evil", raw={})

    app.dependency_overrides[require_cf_user] = _cf_user_fake

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/whoami")
    assert r.status_code == 403
    assert "not invited" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_require_user_403_on_deactivated(
    app_with_db: tuple[FastAPI, async_sessionmaker, AsyncEngine],
) -> None:
    app, factory, _ = app_with_db

    async with factory() as s:
        s.add(
            User(
                email="banned@example.com",
                display_name="Banned",
                is_active=False,
            )
        )
        await s.commit()

    @app.get("/whoami")
    async def whoami(user: User = Depends(require_user)) -> dict:
        return {"id": user.id}

    async def _cf_user_fake() -> CFAccessUser:
        return CFAccessUser(email="banned@example.com", sub="b", raw={})

    app.dependency_overrides[require_cf_user] = _cf_user_fake

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/whoami")
    assert r.status_code == 403
    assert "deactivated" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_require_admin_403_when_not_admin(
    app_with_db: tuple[FastAPI, async_sessionmaker, AsyncEngine],
) -> None:
    app, factory, _ = app_with_db

    async with factory() as s:
        s.add(User(email="admin@example.com", display_name="A", is_admin=True))
        s.add(User(email="user@example.com", display_name="U", is_admin=False))
        await s.commit()

    @app.get("/admin-only")
    async def admin_only(user: User = Depends(require_admin)) -> dict:
        return {"ok": True}

    async def _user_jwt() -> CFAccessUser:
        return CFAccessUser(email="user@example.com", sub="u", raw={})

    app.dependency_overrides[require_cf_user] = _user_jwt

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/admin-only")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_passes_when_admin(
    app_with_db: tuple[FastAPI, async_sessionmaker, AsyncEngine],
) -> None:
    app, factory, _ = app_with_db

    async with factory() as s:
        s.add(User(email="admin@example.com", display_name="A", is_admin=True))
        await s.commit()

    @app.get("/admin-only")
    async def admin_only(user: User = Depends(require_admin)) -> dict:
        return {"ok": True, "id": user.id}

    async def _admin_jwt() -> CFAccessUser:
        return CFAccessUser(email="admin@example.com", sub="a", raw={})

    app.dependency_overrides[require_cf_user] = _admin_jwt

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/admin-only")
    assert r.status_code == 200
    assert r.json()["ok"] is True
