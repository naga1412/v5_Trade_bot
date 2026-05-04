"""Admin REST endpoints (SP-0.7 Phase G).

All routes under /api/v1/admin/* are gated by `Depends(require_admin)`. Spec
§5 — admin REST surface for user CRUD, invitations, impersonation start/stop,
unified audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from app.api.schemas import (
    ImpersonationStartOut,
    InvitationCreateIn,
    InvitationOut,
    UserOut,
    UserPatchIn,
)
from app.auth.deps import require_admin
from app.auth.impersonation import (
    clear_active_target,
    log_event,
    set_active_target,
)
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


@router.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: int,
    body: UserPatchIn,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> UserOut:
    """Toggle is_active / is_admin / notes on a user. Cannot demote self."""
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    # Self-protection: cannot demote yourself.
    if (
        body.is_admin is False
        and current_admin.id == user_id
    ):
        raise HTTPException(
            status_code=400, detail="cannot demote yourself from admin",
        )

    if body.is_active is not None:
        target.is_active = body.is_active
    if body.is_admin is not None:
        target.is_admin = body.is_admin
    if body.notes is not None:
        target.notes = body.notes

    await session.commit()
    await session.refresh(target)
    return _user_to_out(target)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_user(
    user_id: int,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Soft-delete: set is_active=False. Cannot delete self."""
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    target.is_active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/impersonate/{user_id}", response_model=ImpersonationStartOut)
async def start_impersonation(
    user_id: int,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ImpersonationStartOut:
    """Spec §9.2: write impersonation_state row + log start event."""
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="cannot impersonate yourself")
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="target user is inactive")
    if target.is_admin:
        raise HTTPException(
            status_code=400, detail="cannot impersonate another admin",
        )
    await set_active_target(
        session, admin_id=current_admin.id, target_id=target.id,
    )
    await log_event(
        session,
        admin_user_id=current_admin.id,
        target_user_id=target.id,
        action="start",
        request_path=f"/api/v1/admin/impersonate/{user_id}",
    )
    await session.commit()
    return ImpersonationStartOut(
        admin_user_id=current_admin.id,
        target_user_id=target.id,
        started_at=datetime.now(timezone.utc),
    )


@router.delete(
    "/impersonate",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def stop_impersonation(
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Spec §9.2: clear impersonation_state + log stop event. Idempotent."""
    # Best-effort: read previous target from state to label the event.
    row = (
        await session.execute(
            sa.text(
                "SELECT target_user_id FROM impersonation_state "
                "WHERE admin_user_id = :a"
            ),
            {"a": current_admin.id},
        )
    ).first()
    if row is not None:
        await clear_active_target(session, admin_id=current_admin.id)
        await log_event(
            session,
            admin_user_id=current_admin.id,
            target_user_id=row.target_user_id,
            action="stop",
            request_path="/api/v1/admin/impersonate",
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
