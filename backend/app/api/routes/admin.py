"""Admin REST endpoints (SP-0.7 Phase G).

All routes under /api/v1/admin/* are gated by `Depends(require_admin)`. Spec
§5 — admin REST surface for user CRUD, invitations, impersonation start/stop,
unified audit trail.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import timezone

from datetime import datetime
from typing import Any, Literal

from app.api.schemas import (
    AuditTrailEntry,
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


# Tables exposed by the audit-trail endpoint. Each entry maps to a
# (table_name, ts_column, summary_template, has_user_id) tuple. The
# summary_template uses %s placeholders we substitute server-side.
_AuditSrc = tuple[str, str, str, bool]
_AUDIT_TABLES: dict[str, _AuditSrc] = {
    "predictions": (
        "predictions", "ts",
        "{symbol} {timeframe} {direction} score={final_score}", True,
    ),
    "paper_trades": (
        "paper_trades", "opened_at",
        "{symbol} {direction} entry={entry_price}", True,
    ),
    "shadow_trades": (
        "shadow_trades", "opened_at",
        "{symbol} {timeframe} {direction}", True,
    ),
    "auth_violations": (
        "auth_violations", "attempted_at",
        "{attempted_email} reason={reason}", False,
    ),
    "impersonation_events": (
        "impersonation_events", "ts",
        "admin={admin_user_id} -> target={target_user_id} action={action}",
        False,
    ),
}


def _coerce_ts(value: Any) -> datetime:
    """Coerce a DB ts (datetime or ISO string) to a tz-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _summarize(row: Any, template: str) -> str:
    """Render the summary template by mapping the row's columns -> values."""
    mapping = {k: getattr(row, k, "?") for k in row._mapping.keys()}
    try:
        return template.format(**mapping)
    except Exception:  # noqa: BLE001
        return template


async def _select_audit_rows(
    session: AsyncSession,
    *,
    table: str,
    cols: list[str],
    user_filter: int | None,
    ts_col: str,
    has_user_id: bool,
    since: datetime | None,
) -> list[Any]:
    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if since is not None:
        where_parts.append(f"{ts_col} >= :since")
        params["since"] = since
    if user_filter is not None and has_user_id:
        where_parts.append("user_id = :uid")
        params["uid"] = user_filter
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = (
        f"SELECT id, {', '.join(cols)}, {ts_col} AS _ts "
        f"FROM {table}{where_sql} ORDER BY {ts_col} DESC"
    )
    return list((await session.execute(sa.text(sql), params)).all())


@router.get("/audit-trail", response_model=list[AuditTrailEntry])
async def audit_trail(
    user_id: int | None = Query(default=None),
    table: Literal[
        "predictions", "paper_trades", "shadow_trades",
        "auth_violations", "impersonation_events",
    ] | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AuditTrailEntry]:
    """Unified, sortable, paginated view across audit-relevant tables."""
    targets = (
        {table: _AUDIT_TABLES[table]} if table is not None else _AUDIT_TABLES
    )

    # When user_id filter is set, system-only tables (auth_violations,
    # impersonation_events) are excluded since they aren't user-scoped.
    if user_id is not None:
        targets = {k: v for k, v in targets.items() if v[3]}

    # Per-table column projections used both for selection + summary template
    # variable resolution.
    cols_by_table: dict[str, list[str]] = {
        "predictions": ["user_id", "symbol", "timeframe", "direction", "final_score"],
        "paper_trades": ["user_id", "symbol", "direction", "entry_price"],
        "shadow_trades": ["user_id", "symbol", "timeframe", "direction"],
        "auth_violations": ["attempted_email", "reason"],
        "impersonation_events": ["admin_user_id", "target_user_id", "action"],
    }

    entries: list[AuditTrailEntry] = []
    for key, (tname, ts_col, template, has_user_id) in targets.items():
        rows = await _select_audit_rows(
            session,
            table=tname,
            cols=cols_by_table[key],
            user_filter=user_id,
            ts_col=ts_col,
            has_user_id=has_user_id,
            since=since,
        )
        for r in rows:
            uid_val = getattr(r, "user_id", None) if has_user_id else None
            entries.append(AuditTrailEntry(
                table_name=tname,
                row_id=r.id,
                user_id=uid_val,
                ts=_coerce_ts(r._ts),
                summary=_summarize(r, template),
            ))

    entries.sort(key=lambda e: e.ts, reverse=True)
    return entries[offset : offset + limit]
