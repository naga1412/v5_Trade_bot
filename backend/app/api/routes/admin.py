"""Admin REST endpoints (SP-0.7 Phase G).

All routes under /api/v1/admin/* are gated by `Depends(require_admin)`. Spec
§5 — admin REST surface for user CRUD, invitations, impersonation start/stop,
unified audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    InvitationCreateIn,
    InvitationOut,
    UserOut,
)
from app.auth.deps import require_admin
from app.auth.models import PendingInvitation, User
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _user_to_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        is_admin=u.is_admin,
        is_active=u.is_active,
        trading_mode=u.trading_mode,
        last_login=u.last_login,
        created_at=u.created_at,
        invited_by=u.invited_by,
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[UserOut]:
    """Return all users (incl. deactivated) sorted by created_at ASC."""
    rows = (
        await session.execute(
            sa.select(User).order_by(User.created_at.asc(), User.id.asc())
        )
    ).scalars().all()
    return [_user_to_out(u) for u in rows]


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _invitation_to_out(inv: PendingInvitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        display_name=inv.display_name,
        invited_by=inv.invited_by,
        invited_at=inv.invited_at,
        accepted_at=inv.accepted_at,
        cf_access_added=inv.cf_access_added,
    )


@router.post(
    "/invitations",
    response_model=InvitationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    body: InvitationCreateIn,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> InvitationOut:
    """Insert a pending_invitation row, owned by the current admin."""
    norm = _norm_email(body.email)
    if not norm:
        raise HTTPException(status_code=400, detail="email is required")

    existing_user = (
        await session.execute(sa.select(User).where(User.email == norm))
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="user with this email exists")

    existing_inv = (
        await session.execute(
            sa.select(PendingInvitation).where(PendingInvitation.email == norm)
        )
    ).scalar_one_or_none()
    if existing_inv is not None:
        raise HTTPException(
            status_code=409, detail="invitation for this email already exists",
        )

    inv = PendingInvitation(
        email=norm,
        display_name=body.display_name,
        invited_by=current_admin.id,
        is_admin=body.is_admin,
        notes=body.notes,
    )
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return _invitation_to_out(inv)
