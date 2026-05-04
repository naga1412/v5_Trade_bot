"""Failing tests for spec §4.2 first-time-login rules."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.auth.models import AuthViolation, Base, PendingInvitation, User
from app.auth.users import (
    UserDeactivatedError,  # noqa: F401 — exported symbol smoke import
    UserNotInvitedError,
    get_or_create_user_from_email,
)


@pytest_asyncio.fixture
async def engine_with_tables() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_user_becomes_admin(engine_with_tables: AsyncEngine) -> None:
    """Spec §4.2 case 1: empty users table -> first login is auto-admin."""
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        user = await get_or_create_user_from_email(
            session, email="first@example.com", display_name="First User",
        )
        await session.commit()

    assert user.is_admin is True
    assert user.is_active is True
    assert user.email == "first@example.com"


@pytest.mark.asyncio
async def test_invited_user_login_creates_user_and_marks_invitation_accepted(
    engine_with_tables: AsyncEngine,
) -> None:
    """Spec §4.2 case 2: email present in pending_invitations."""
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        admin = User(email="admin@example.com", display_name="Admin", is_admin=True)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        inv = PendingInvitation(
            email="friend@example.com", display_name="Friend",
            invited_by=admin.id, is_admin=False,
        )
        session.add(inv)
        await session.commit()
        admin_id = admin.id

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        user = await get_or_create_user_from_email(
            session, email="friend@example.com", display_name="Friend From Google",
        )
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        loaded_inv = (await session.execute(
            sa.select(PendingInvitation).where(
                PendingInvitation.email == "friend@example.com"
            )
        )).scalar_one()

    assert user.is_admin is False
    assert user.invited_by == admin_id
    assert user.display_name == "Friend"  # invitation display_name wins over JWT
    assert loaded_inv.accepted_at is not None


@pytest.mark.asyncio
async def test_uninvited_user_login_raises_and_logs_auth_violation(
    engine_with_tables: AsyncEngine,
) -> None:
    """Spec §4.2 case 3: not in invitations + table not empty -> 403 + audit log."""
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        admin = User(email="admin@example.com", display_name="Admin", is_admin=True)
        session.add(admin)
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        with pytest.raises(UserNotInvitedError):
            await get_or_create_user_from_email(
                session, email="hacker@evil.com", display_name="Hacker",
            )
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        violations = (
            await session.execute(sa.select(AuthViolation))
        ).scalars().all()

    assert len(violations) == 1
    assert violations[0].attempted_email == "hacker@evil.com"
    assert violations[0].reason == "not_invited"


@pytest.mark.asyncio
async def test_returning_user_updates_last_login(
    engine_with_tables: AsyncEngine,
) -> None:
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        await get_or_create_user_from_email(
            session, email="alice@example.com", display_name="Alice",
        )
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        user2 = await get_or_create_user_from_email(
            session, email="alice@example.com", display_name="Alice",
        )
        await session.commit()

    assert user2.last_login is not None


@pytest.mark.asyncio
async def test_deactivated_user_helper_returns_row_not_flipped(
    engine_with_tables: AsyncEngine,
) -> None:
    """Helper preserves is_active=False; the dep layer (require_user) raises 403."""
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        user = User(
            email="banned@example.com", display_name="Banned", is_active=False,
        )
        session.add(user)
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        loaded = await get_or_create_user_from_email(
            session, email="banned@example.com", display_name="Banned",
        )

    assert loaded.is_active is False  # helper preserves it; dep raises on it


@pytest.mark.asyncio
async def test_email_normalization_case_insensitive(
    engine_with_tables: AsyncEngine,
) -> None:
    """Spec ambiguity #1: email comparison MUST be case-insensitive."""
    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        await get_or_create_user_from_email(
            session, email="MixedCase@Example.com", display_name="Mixed",
        )
        await session.commit()

    async with AsyncSession(engine_with_tables, expire_on_commit=False) as session:
        # Lookup via different casing -> same user, no duplicate row.
        again = await get_or_create_user_from_email(
            session, email="mixedcase@example.com", display_name="Mixed",
        )
        await session.commit()
        n_users = (
            await session.execute(sa.select(sa.func.count()).select_from(User))
        ).scalar_one()

    assert n_users == 1
    assert again.email == "mixedcase@example.com"
