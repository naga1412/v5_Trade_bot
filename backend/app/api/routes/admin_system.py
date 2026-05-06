"""Admin REST for SP-PAUSE master pause/resume.

Four routes, all gated by :func:`app.auth.deps.require_admin`:

* ``POST /api/v1/admin/system/pause``     — body ``{reason: str}``
* ``POST /api/v1/admin/system/resume``    — body ``{}``
* ``GET  /api/v1/admin/system/state``     — current state
* ``GET  /api/v1/admin/system/log?limit`` — last 50 pause/resume events

The pause/resume routes both flow through :func:`app.ops.pause_state.set_paused`,
which atomically updates Redis + appends a row to ``auth_violations``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    SystemPauseEventListOut,
    SystemPauseEventOut,
    SystemPauseRequest,
    SystemPauseStateOut,
)
from app.auth.deps import require_admin
from app.auth.models import User
from app.db.session import get_session
from app.ops import pause_state


log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/system",
    tags=["admin-system"],
    dependencies=[Depends(require_admin)],
)


def _state_to_out(state: pause_state.SystemPauseState) -> SystemPauseStateOut:
    return SystemPauseStateOut(
        paused=state.paused,
        since=state.since,
        by_email=state.by_email,
        reason=state.reason,
    )


@router.post("/pause", response_model=SystemPauseStateOut)
async def pause_system(
    body: SystemPauseRequest,
    actor: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseStateOut:
    await pause_state.set_paused(
        True, by_email=actor.email, reason=body.reason,
        session=session, request_path="/api/v1/admin/system/pause",
    )
    return _state_to_out(await pause_state.get_state())


@router.post("/resume", response_model=SystemPauseStateOut)
async def resume_system(
    actor: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseStateOut:
    await pause_state.set_paused(
        False, by_email=actor.email, reason=None,
        session=session, request_path="/api/v1/admin/system/resume",
    )
    return _state_to_out(await pause_state.get_state())


@router.get("/state", response_model=SystemPauseStateOut)
async def get_system_state() -> SystemPauseStateOut:
    return _state_to_out(await pause_state.get_state())


@router.get("/log", response_model=SystemPauseEventListOut)
async def get_system_log(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SystemPauseEventListOut:
    events = await pause_state.pause_event_log(session, limit=limit)
    return SystemPauseEventListOut(
        events=[
            SystemPauseEventOut(
                id=e.id, kind=e.kind, by_email=e.by_email,
                at=e.at, reason=e.reason,
            )
            for e in events
        ],
    )
