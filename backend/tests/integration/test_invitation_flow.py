"""Phase L2: full admin invitation flow end-to-end.

Spec §4.2 + §4.3 — admin POSTs to /admin/invitations, friend's first hit on
any auth-gated route triggers `require_user`, which finds the pending
invitation and materialises a `users` row with `invited_by` set + flips the
invitation's `accepted_at` timestamp.

Two distinct clients share the same in-memory engine so the invitation row
written by the admin client is visible to the friend client's user-loader.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import PendingInvitation, User


def _install_friend_jwt_overrides(
    auth_factory: async_sessionmaker[AsyncSession],
    *, email: str = "newbie@x.com", name: str = "Newbie From CF",
    sub: str = "newbie-jwt-sub",
) -> None:
    """Install overrides so the next request comes in as `email` via CF JWT.

    Removes any prior `require_user`/`require_admin`/`current_user_or_impersonated`
    overrides so the *real* user-loader runs end-to-end on `auth_factory`.
    """
    from app.auth.deps import (
        current_user_or_impersonated,
        require_admin,
        require_user,
    )
    from app.db.session import get_session
    from app.deps import CFAccessUser, require_cf_user
    from app.main import app

    async def _gen_session() -> AsyncIterator[AsyncSession]:
        async with auth_factory() as session:
            yield session

    async def _cf() -> CFAccessUser:
        return CFAccessUser(email=email, sub=sub, raw={"email": email, "name": name})

    # Drop any prior overrides for the user-resolving deps so the real
    # require_user runs on this request and triggers the loader.
    for d in (require_user, require_admin, current_user_or_impersonated):
        app.dependency_overrides.pop(d, None)

    app.dependency_overrides[get_session] = _gen_session
    app.dependency_overrides[require_cf_user] = _cf


def _clear_jwt_overrides() -> None:
    from app.auth.deps import (
        current_user_or_impersonated,
        require_admin,
        require_user,
    )
    from app.db.session import get_session
    from app.deps import require_cf_user
    from app.main import app

    for d in (
        get_session, require_cf_user, require_user,
        require_admin, current_user_or_impersonated,
    ):
        app.dependency_overrides.pop(d, None)


@pytest.mark.asyncio
async def test_full_invitation_flow_creates_user_and_marks_accepted(
    admin_client: httpx.AsyncClient,
    auth_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Admin invites newbie@x.com -> newbie hits /me -> users row appears."""
    from app.main import app

    # --- Step 1: admin creates the invitation. ---
    r1 = await admin_client.post(
        "/api/v1/admin/invitations",
        json={
            "email": "newbie@x.com",
            "display_name": "Newbie",
            "is_admin": False,
            "notes": "L2 invitation flow test",
        },
    )
    assert r1.status_code == 201, r1.text

    # Pre-condition: no users row for newbie@x.com yet.
    async with auth_factory() as s:
        pre = (
            await s.execute(
                sa.select(User).where(User.email == "newbie@x.com")
            )
        ).scalar_one_or_none()
    assert pre is None, "user must not exist before first login"

    # --- Step 2: friend hits an auth-gated route for the first time. ---
    # Swap the admin overrides for a CF-only override so the real
    # require_user runs end-to-end on the shared auth_factory engine.
    _install_friend_jwt_overrides(auth_factory)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r2 = await c.get("/api/v1/me")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["email"] == "newbie@x.com"
        assert body["is_admin"] is False
    finally:
        _clear_jwt_overrides()

    # --- Step 3: assert users row created with invited_by=admin.id. ---
    async with auth_factory() as s:
        new_user = (
            await s.execute(
                sa.select(User).where(User.email == "newbie@x.com")
            )
        ).scalar_one()
        assert new_user.invited_by == 1, (
            f"expected invited_by=admin (1), got {new_user.invited_by!r}"
        )
        assert new_user.is_admin is False
        assert new_user.is_active is True
        assert new_user.display_name == "Newbie"

        # --- Step 4: invitation row's accepted_at populated. ---
        invitation = (
            await s.execute(
                sa.select(PendingInvitation).where(
                    PendingInvitation.email == "newbie@x.com"
                )
            )
        ).scalar_one()
        assert invitation.accepted_at is not None, (
            "invitation.accepted_at must be set after first login"
        )
        assert invitation.invited_by == 1


@pytest.mark.asyncio
async def test_uninvited_first_login_is_rejected_403(
    auth_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spec §4.2 case 3: a JWT email with no invitation gets 403 + audit row."""
    from app.auth.models import AuthViolation
    from app.main import app

    _install_friend_jwt_overrides(
        auth_factory, email="rando@x.com", name="Rando", sub="rando",
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/me")
        assert r.status_code == 403, r.text

        # AuthViolation row should be persisted as the audit signal.
        async with auth_factory() as s:
            violations = list(
                (
                    await s.execute(
                        sa.select(AuthViolation).where(
                            AuthViolation.attempted_email == "rando@x.com"
                        )
                    )
                ).scalars().all()
            )
        assert len(violations) >= 1, "uninvited login must persist audit row"
    finally:
        _clear_jwt_overrides()
