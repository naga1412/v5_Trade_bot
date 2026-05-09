"""Self-service /api/v1/me endpoints (SP-0.7 Phase H).

GET/PATCH /me are gated by `current_user_or_impersonated`. Per spec §9.2 the
admin-impersonation context is read-only; mutating endpoints (PATCH /me, POST
/me/binance-keys, /me/telegram, /me/totp/setup, /me/totp/verify) reject the
request with 403 if an admin is currently impersonating someone else.
"""

from __future__ import annotations

from datetime import time

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

import json

from app.api.schemas import (
    BinanceKeysIn,
    KillSwitchListOut,
    KillSwitchOut,
    KillSwitchPatchIn,
    MeOut,
    MePatchIn,
    TelegramIn,
    TotpSetupOut,
    TotpVerifyIn,
    TotpVerifyOut,
    TradingModeChangeIn,
    TradingModeChangeOut,
)
from app.auth.deps import current_user_or_impersonated, require_user
from app.auth.impersonation import get_active_target
from app.auth.models import User
from app.auth.secrets import (
    decrypt_for_user,
    encrypt_for_user,
    set_binance_keys,
    set_telegram,
)
from app.auth.totp import generate_backup_codes, generate_totp_setup, verify_totp
from app.config import get_settings
from app.db.session import get_session
from app.trading.kill_switches import DEFAULTS as KILL_DEFAULTS
from app.trading.modes import (
    ModeChangeError,
    get_mode,
    is_upgrade,
    set_mode,
)
from app.trading.promotion import compute_gates_from_db

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


@router.post(
    "/binance-keys",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def set_me_binance_keys(
    body: BinanceKeysIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Encrypt + persist binance api_key + api_secret for the current user."""
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    await set_binance_keys(
        session,
        user=user,
        api_key=body.api_key,
        api_secret=body.api_secret,
        passphrase=get_settings().master_passphrase,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/telegram",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def set_me_telegram(
    body: TelegramIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    """Encrypt + persist telegram bot_token; chat_id stored plain."""
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    await set_telegram(
        session,
        user=user,
        bot_token=body.bot_token,
        chat_id=body.chat_id,
        passphrase=get_settings().master_passphrase,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/totp/setup", response_model=TotpSetupOut)
async def setup_me_totp(
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TotpSetupOut:
    """Generate a fresh TOTP secret + backup codes; encrypt + persist.

    Returns the secret and backup codes once (only at setup time) so the UI
    can render the QR + display recovery codes for the user to save.
    """
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    setup = generate_totp_setup(account_email=user.email)
    backup_codes = generate_backup_codes()
    passphrase = get_settings().master_passphrase

    user.totp_secret_encrypted = encrypt_for_user(
        setup.secret, passphrase=passphrase, user_id=user.id,
    )
    user.totp_backup_codes_encrypted = encrypt_for_user(
        json.dumps(backup_codes), passphrase=passphrase, user_id=user.id,
    )
    await session.commit()

    return TotpSetupOut(
        provisioning_uri=setup.provisioning_uri,
        secret_for_display=setup.secret,
        backup_codes=backup_codes,
    )


@router.patch("/trading-mode", response_model=TradingModeChangeOut)
async def change_trading_mode(
    body: TradingModeChangeIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TradingModeChangeOut:
    """SP-8 Phase J — switch the user's trading_mode.

    Downgrades require nothing. Upgrades require:
      1. Spec §4 gates pass for the target mode (computed server-side
         from shadow_trades over the rolling 30-day window).
      2. A valid TOTP code (so the upgrade ties to physical possession
         of the operator's authenticator).

    Audit row is hash-chained into mode_change_log; row_hash is
    returned so the UI can link it.
    """
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    old = await get_mode(session, user.id)
    upgrade = is_upgrade(old, body.new_mode)

    snapshot = None
    if upgrade:
        # Hardware-confirm: TOTP required for any promotion.
        if not body.totp_code or not user.totp_secret_encrypted:
            raise HTTPException(
                status_code=400,
                detail=(
                    "TOTP code required for upgrades. Configure TOTP at "
                    "/me/totp/setup and submit the current 6-digit code."
                ),
            )
        secret = decrypt_for_user(
            user.totp_secret_encrypted,
            passphrase=get_settings().master_passphrase,
            user_id=user.id,
        )
        if not verify_totp(secret, body.totp_code):
            raise HTTPException(
                status_code=400, detail="invalid TOTP code",
            )
        # Compute gates from DB so the client can't lie about its history.
        snapshot = await compute_gates_from_db(session)

    try:
        row_hash = await set_mode(
            session,
            user_id=user.id,
            new_mode=body.new_mode,
            triggered_by="user",
            reason=body.reason,
            gate_snapshot=snapshot,
        )
    except ModeChangeError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "gate_failed",
                "current_mode": e.refusal.current_mode,
                "requested_mode": e.refusal.requested_mode,
                "failures": e.refusal.gate_failures,
            },
        ) from e

    await session.commit()
    return TradingModeChangeOut(
        new_mode=body.new_mode, old_mode=old,
        audit_row_hash=row_hash, is_upgrade=upgrade,
    )


@router.get("/kill-switches", response_model=KillSwitchListOut)
async def list_me_kill_switches(
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> KillSwitchListOut:
    """SP-8 Phase J — list all 6 kill switches with current state.

    Falls back to spec §11.1 defaults for switches without a row yet.
    """
    rows = (await session.execute(
        sa.text(
            "SELECT switch_name, enabled, threshold_value, is_tripped, "
            "       tripped_at, tripped_reason "
            "FROM kill_switch_state WHERE user_id = :u"
        ),
        {"u": actual_user.id},
    )).all()
    by_name = {r.switch_name: r for r in rows}

    switches = []
    for name, default in KILL_DEFAULTS.items():
        r = by_name.get(name)
        if r is None:
            switches.append(KillSwitchOut(
                name=name,  # type: ignore[arg-type]
                enabled=True, threshold_value=None,
                is_tripped=False, tripped_at=None, tripped_reason=None,
                default_threshold=default,
            ))
        else:
            switches.append(KillSwitchOut(
                name=name,  # type: ignore[arg-type]
                enabled=bool(r.enabled),
                threshold_value=r.threshold_value,
                is_tripped=bool(r.is_tripped),
                tripped_at=r.tripped_at,
                tripped_reason=r.tripped_reason,
                default_threshold=default,
            ))
    return KillSwitchListOut(switches=switches)


@router.patch("/kill-switches/{name}", response_model=KillSwitchOut)
async def patch_me_kill_switch(
    name: str,
    body: KillSwitchPatchIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> KillSwitchOut:
    """SP-8 Phase J — toggle a kill switch or update its threshold.

    Disabling a switch is a safety-critical change — it requires a
    valid TOTP code. Enabling, or threshold-only edits, do not.
    """
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    if name not in KILL_DEFAULTS:
        raise HTTPException(status_code=404, detail=f"unknown switch: {name}")

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    # TOTP required when disabling.
    if body.enabled is False:
        if not body.totp_code or not user.totp_secret_encrypted:
            raise HTTPException(
                status_code=400,
                detail="TOTP required to disable a kill switch",
            )
        secret = decrypt_for_user(
            user.totp_secret_encrypted,
            passphrase=get_settings().master_passphrase,
            user_id=user.id,
        )
        if not verify_totp(secret, body.totp_code):
            raise HTTPException(status_code=400, detail="invalid TOTP code")

    # Upsert the row. Read existing first so we keep the unchanged fields.
    row = (await session.execute(
        sa.text(
            "SELECT enabled, threshold_value FROM kill_switch_state "
            "WHERE user_id = :u AND switch_name = :n"
        ),
        {"u": user.id, "n": name},
    )).first()
    new_enabled = body.enabled if body.enabled is not None else (
        bool(row.enabled) if row is not None else True
    )
    new_threshold = (
        body.threshold_value if body.threshold_value is not None else (
            row.threshold_value if row is not None else None
        )
    )
    if row is None:
        await session.execute(
            sa.text(
                "INSERT INTO kill_switch_state "
                "(user_id, switch_name, enabled, threshold_value) "
                "VALUES (:u, :n, :e, :t)"
            ),
            {"u": user.id, "n": name, "e": new_enabled, "t": new_threshold},
        )
    else:
        await session.execute(
            sa.text(
                "UPDATE kill_switch_state "
                "SET enabled = :e, threshold_value = :t, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = :u AND switch_name = :n"
            ),
            {"u": user.id, "n": name, "e": new_enabled, "t": new_threshold},
        )
    await session.commit()
    return KillSwitchOut(
        name=name,  # type: ignore[arg-type]
        enabled=new_enabled, threshold_value=new_threshold,
        is_tripped=False, tripped_at=None, tripped_reason=None,
        default_threshold=KILL_DEFAULTS[name],  # type: ignore[index]
    )


@router.post("/totp/verify", response_model=TotpVerifyOut)
async def verify_me_totp(
    body: TotpVerifyIn,
    request: Request,
    actual_user: User = Depends(require_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TotpVerifyOut:
    """Verify a TOTP `code` against the user's stored secret."""
    is_imp = await _is_currently_impersonating(request, actual_user, session)
    _reject_during_impersonation(is_imp)

    user = (
        await session.execute(
            sa.select(User).where(User.id == actual_user.id)
        )
    ).scalar_one()

    if not user.totp_secret_encrypted:
        raise HTTPException(
            status_code=400, detail="TOTP not configured; call /me/totp/setup first",
        )
    secret = decrypt_for_user(
        user.totp_secret_encrypted,
        passphrase=get_settings().master_passphrase,
        user_id=user.id,
    )
    return TotpVerifyOut(ok=verify_totp(secret, body.code))
