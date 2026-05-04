"""FastAPI deps that resolve the CF Access JWT to a `users` row.

Layered on top of `app.deps.require_cf_user` so the JWT verification path is
unchanged.

- `require_user` returns the `User` row, creating it on first login per
  spec §4.2, raising 403 on uninvited or deactivated users.
- `require_admin` returns the same `User` row but adds `is_admin=True` check.
- `current_user_or_impersonated` returns the `User` whose data the request
  should see — the actual user for self-service routes, or the impersonated
  user when an admin has set `view_as_user_id`.

Spec §4.4 dev-mode: `require_cf_user` already returns a stable `dev@local`
CFAccessUser in `ENV=development`. `require_user` then maps that to a
`dev@local` `users` row (created on demand as bootstrap admin if the table
is empty).
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.users import (
    UserNotInvitedError,
    get_or_create_user_from_email,
)
from app.db.session import get_session
from app.deps import CFAccessUser, require_cf_user


async def require_user(
    cf_user: CFAccessUser = Depends(require_cf_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> User:
    """Resolve the JWT email to a `users` row. Commits on success."""
    display_name = cf_user.raw.get("name") or cf_user.email or "User"
    try:
        user = await get_or_create_user_from_email(
            session, email=cf_user.email, display_name=display_name,
        )
    except UserNotInvitedError as e:
        # Session may have an AuthViolation row — commit so we keep the audit signal.
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not invited. Contact your administrator.",
        ) from e

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated.",
        )

    await session.commit()
    return user


async def require_admin(
    user: User = Depends(require_user),  # noqa: B008
) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


async def current_user_or_impersonated(
    request: Request,
    user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> User:
    """Return the user whose data should be shown.

    For admins with an active impersonation flag, returns the target user.
    For everyone else, returns themselves. Logs `impersonation_event` rows
    (action='page_view') when impersonation is active.
    """
    # Lazy import to avoid circular import at module load time.
    from app.auth.impersonation import get_active_target, log_event

    target_id = await get_active_target(request, admin=user, _session=session)
    if target_id is None:
        return user

    if not user.is_admin:
        # Defensive: only admins can have an impersonation flag.
        return user

    target = await session.get(User, target_id)
    if target is None or not target.is_active:
        return user

    await log_event(
        session,
        admin_user_id=user.id,
        target_user_id=target.id,
        action="page_view",
        request_path=str(request.url.path),
    )
    await session.commit()
    return target
