"""Admin REST endpoints (SP-0.7 Phase G).

All routes under /api/v1/admin/* are gated by `Depends(require_admin)`. Spec
§5 — admin REST surface for user CRUD, invitations, impersonation start/stop,
unified audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import UserOut
from app.auth.deps import require_admin
from app.auth.models import User
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
