"""User loader: maps Cloudflare JWT email to a `users` row.

Implements spec §4.2 first-time-login rules:
1. users table empty -> create as admin (bootstrap)
2. email in pending_invitations -> create as user, link invited_by, mark accepted
3. otherwise -> log auth_violations row, raise UserNotInvitedError

Email comparison is case-insensitive (spec ambiguity #1, resolved in plan):
emails are normalized to lowercase before write and on every lookup.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthViolation, PendingInvitation, User


class UserNotInvitedError(Exception):
    """Email was not found in `pending_invitations` and `users` is non-empty."""


class UserDeactivatedError(Exception):
    """User exists but `is_active=False`."""


# Spec §4.4: dev bypass user. Always allowed in ENV=development; auto-created
# as admin if the row doesn't exist (regardless of whether the users table is
# otherwise empty — the migration seed of the bootstrap admin would otherwise
# starve dev@local out of case 1).
DEV_BYPASS_EMAIL = "dev@local"


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


async def get_or_create_user_from_email(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
) -> User:
    """Resolve a JWT email to a `users` row, applying spec §4.2 rules.

    Caller commits the session. Returns a `User` ORM object that may have
    `is_active=False` — the dep layer is responsible for converting that to a 403.
    """
    norm_email = _normalize_email(email)
    if not norm_email:
        raise UserNotInvitedError("empty email in JWT")

    existing = (
        await session.execute(sa.select(User).where(User.email == norm_email))
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_login = datetime.now(timezone.utc)
        return existing

    # Email is unknown. Decide between bootstrap, invitation acceptance, or refusal.
    n_users = (
        await session.execute(sa.select(sa.func.count()).select_from(User))
    ).scalar_one()

    if n_users == 0 or norm_email == DEV_BYPASS_EMAIL:
        # Spec §4.2 case 1 (bootstrap) OR §4.4 (dev bypass).
        # The dev bypass branch ensures `dev@local` always gets an admin row even
        # when the migration has already seeded the production bootstrap admin.
        new_user = User(
            email=norm_email,
            display_name=display_name or norm_email,
            is_admin=True,
            is_active=True,
            last_login=datetime.now(timezone.utc),
        )
        session.add(new_user)
        await session.flush()
        return new_user

    invitation = (
        await session.execute(
            sa.select(PendingInvitation).where(
                PendingInvitation.email == norm_email,
                PendingInvitation.accepted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if invitation is not None:
        # Spec §4.2 case 2: invited user accepts.
        new_user = User(
            email=norm_email,
            display_name=invitation.display_name or display_name or norm_email,
            is_admin=invitation.is_admin,
            is_active=True,
            invited_by=invitation.invited_by,
            last_login=datetime.now(timezone.utc),
        )
        session.add(new_user)
        await session.flush()
        invitation.accepted_at = datetime.now(timezone.utc)
        return new_user

    # Spec §4.2 case 3: refuse + log.
    session.add(AuthViolation(attempted_email=norm_email, reason="not_invited"))
    await session.flush()
    raise UserNotInvitedError(f"email {norm_email} not invited")
