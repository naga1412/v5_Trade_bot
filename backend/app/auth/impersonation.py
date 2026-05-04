"""Impersonation: server-state flag store + audit logger.

Spec §9.2 — admin clicks "View as user" -> server stores `view_as_user_id`
keyed on the admin's user_id (one row per admin in `impersonation_state`).
Subsequent requests routed through `current_user_or_impersonated` see data
through the target user's lens.

Read-only by design: any route with side effects must call `require_user`
(the actual admin), not `current_user_or_impersonated`.
"""

import sqlalchemy as sa
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import ImpersonationEvent, User


async def set_active_target(
    session: AsyncSession,
    *,
    admin_id: int,
    target_id: int,
) -> None:
    """Upsert the active impersonation target for this admin."""
    # SQLite + Postgres both support ON CONFLICT for the test/prod parity we need.
    await session.execute(
        sa.text(
            "INSERT INTO impersonation_state (admin_user_id, target_user_id) "
            "VALUES (:a, :t) "
            "ON CONFLICT(admin_user_id) DO UPDATE SET "
            "target_user_id = excluded.target_user_id, "
            "started_at = CURRENT_TIMESTAMP"
        ),
        {"a": admin_id, "t": target_id},
    )


async def clear_active_target(session: AsyncSession, *, admin_id: int) -> None:
    await session.execute(
        sa.text(
            "DELETE FROM impersonation_state WHERE admin_user_id = :a"
        ),
        {"a": admin_id},
    )


async def get_active_target(
    request: Request,
    *,
    admin: User,
    _session: AsyncSession | None = None,
) -> int | None:
    """Return the target user_id that `admin` is currently impersonating, or None.

    `_session` is injected for testing; in production it's pulled from the
    request state set by the get_session dep upstream.
    """
    session: AsyncSession | None = _session
    if session is None:
        session = (
            request.state.session if hasattr(request.state, "session") else None
        )
    if session is None:
        return None

    row = (
        await session.execute(
            sa.text(
                "SELECT target_user_id FROM impersonation_state "
                "WHERE admin_user_id = :a"
            ),
            {"a": admin.id},
        )
    ).first()
    return row.target_user_id if row else None


async def log_event(
    session: AsyncSession,
    *,
    admin_user_id: int,
    target_user_id: int,
    action: str,
    request_path: str | None = None,
) -> None:
    session.add(
        ImpersonationEvent(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            action=action,
            request_path=request_path,
        )
    )
