"""ORM round-trip on User, PendingInvitation, AuthViolation, ImpersonationEvent."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.models import (
    AuthViolation,
    Base,
    ImpersonationEvent,
    PendingInvitation,
    User,
)


@pytest.mark.asyncio
async def test_user_model_round_trip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        u = User(email="alice@example.com", display_name="Alice", is_admin=False)
        session.add(u)
        await session.commit()
        loaded = (
            await session.execute(
                sa.select(User).where(User.email == "alice@example.com")
            )
        ).scalar_one()
    assert loaded.id == u.id
    assert loaded.is_active is True  # default
    assert loaded.trading_mode == "manual"  # default


@pytest.mark.asyncio
async def test_pending_invitation_model() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        admin = User(email="admin@example.com", display_name="Admin", is_admin=True)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        inv = PendingInvitation(email="bob@example.com", invited_by=admin.id)
        session.add(inv)
        await session.commit()

        loaded = (await session.execute(sa.select(PendingInvitation))).scalar_one()
    assert loaded.email == "bob@example.com"
    assert loaded.accepted_at is None


@pytest.mark.asyncio
async def test_auth_violation_and_impersonation_event() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        v = AuthViolation(attempted_email="hacker@evil.com", reason="not_invited")
        session.add(v)
        admin = User(email="a@x.com", display_name="A", is_admin=True)
        target = User(email="b@x.com", display_name="B")
        session.add_all([admin, target])
        await session.commit()
        await session.refresh(admin)
        await session.refresh(target)
        e = ImpersonationEvent(
            admin_user_id=admin.id, target_user_id=target.id, action="start"
        )
        session.add(e)
        await session.commit()
        n_v = (
            await session.execute(sa.select(sa.func.count()).select_from(AuthViolation))
        ).scalar_one()
        n_e = (
            await session.execute(
                sa.select(sa.func.count()).select_from(ImpersonationEvent)
            )
        ).scalar_one()
    assert n_v == 1
    assert n_e == 1
