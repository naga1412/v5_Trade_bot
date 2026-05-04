"""Self-service /api/v1/me endpoints (SP-0.7 Phase H).

GET/PATCH /me are gated by `current_user_or_impersonated`. Per spec §9.2 the
admin-impersonation context is read-only; mutating endpoints (PATCH /me, POST
/me/binance-keys, /me/telegram, /me/totp/setup, /me/totp/verify) reject the
request with 403 if an admin is currently impersonating someone else.
"""

from __future__ import annotations

from datetime import time

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import MeOut, MePatchIn
from app.auth.deps import current_user_or_impersonated, require_user
from app.auth.impersonation import get_active_target
from app.auth.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/me", tags=["me"])


def _time_to_str(t: time | None) -> str | None:
    return t.isoformat(timespec="minutes") if t is not None else None


def _str_to_time(s: str | None) -> time | None:
    if s is None:
        return None
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _user_to_me(user: User, *, is_impersonating: bool) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        is_impersonating=is_impersonating,
        trading_mode=user.trading_mode,
        position_sizing_mode=user.position_sizing_mode,
        fixed_size_min_usdt=user.fixed_size_min_usdt,
        fixed_size_max_usdt=user.fixed_size_max_usdt,
        max_concurrent_positions=user.max_concurrent_positions,
        max_leverage_cap=user.max_leverage_cap,
        quiet_hours_enabled=user.quiet_hours_enabled,
        quiet_hours_start=_time_to_str(user.quiet_hours_start),
        quiet_hours_end=_time_to_str(user.quiet_hours_end),
        binance_keys_configured=bool(
            user.binance_api_key_encrypted and user.binance_api_secret_encrypted,
        ),
        telegram_configured=bool(
            user.telegram_bot_token_encrypted and user.telegram_chat_id,
        ),
        totp_configured=bool(user.totp_secret_encrypted),
    )


async def _is_currently_impersonating(
    request: Request,
    actual_user: User,
    session: AsyncSession,
) -> bool:
    """True if the actual JWT user is an admin with an active impersonation."""
    if not actual_user.is_admin:
        return False
    target_id = await get_active_target(
        request, admin=actual_user, _session=session,
    )
    return target_id is not None


def _reject_during_impersonation(is_impersonating: bool) -> None:
    if is_impersonating:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Mutations are disabled during impersonation (read-only "
                "view-as-user mode). Stop impersonation first."
            ),
        )


@router.get("", response_model=MeOut)
async def get_me(
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    user: User = Depends(current_user_or_impersonated),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MeOut:
    """Return the current effective user's profile (impersonated if active)."""
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    return _user_to_me(user, is_impersonating=is_imp)


@router.patch("", response_model=MeOut)
async def patch_me(
    body: MePatchIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MeOut:
    """Update mutable profile fields. Rejected during impersonation (§9.2)."""
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    # Re-fetch the actual_user inside this session so we can mutate + commit.
    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.quiet_hours_enabled is not None:
        user.quiet_hours_enabled = body.quiet_hours_enabled
    if body.quiet_hours_start is not None:
        user.quiet_hours_start = _str_to_time(body.quiet_hours_start)
    if body.quiet_hours_end is not None:
        user.quiet_hours_end = _str_to_time(body.quiet_hours_end)
    if body.fixed_size_min_usdt is not None:
        user.fixed_size_min_usdt = body.fixed_size_min_usdt
    if body.fixed_size_max_usdt is not None:
        user.fixed_size_max_usdt = body.fixed_size_max_usdt
    if body.max_concurrent_positions is not None:
        user.max_concurrent_positions = body.max_concurrent_positions
    if body.max_leverage_cap is not None:
        user.max_leverage_cap = body.max_leverage_cap

    await session.commit()
    await session.refresh(user)
    return _user_to_me(user, is_impersonating=False)
