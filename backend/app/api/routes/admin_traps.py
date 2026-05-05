"""Admin REST endpoints for trap enable/disable (SP-5 Phase F2).

Mirrors :mod:`app.api.routes.admin_patterns`: backend-only contract gated by
``Depends(require_admin)`` from SP-0.7. The frontend admin sub-page for traps
is deferred to SP-6 (matches SP-2 + SP-3 deferral pattern).

Schema:
- ``GET /api/v1/admin/traps`` returns one row per trap in
  :data:`app.core.scoring.traps.ALL_TRAPS`, joined against any
  ``trap_enabled`` row at scope ``(*, *)`` so the global default is reflected.
  Traps without a row are reported as enabled.
- ``POST /api/v1/admin/traps/{trap_id}/disable`` upserts a ``trap_enabled``
  row with ``enabled=false`` (optionally scoped by symbol and/or timeframe;
  defaults to the global ``*`` scope).
- ``POST /api/v1/admin/traps/{trap_id}/enable`` removes the matching row(s)
  so the trap returns to its default-enabled state. Idempotent — never 404s
  on already-enabled traps.

The orchestrator ``check_all_traps(...)`` consumes an ``enabled_set`` of
``trap_id`` strings; the in-process resolver that builds that set from the
``trap_enabled`` table lands later (callers can already drive disable rows
via these endpoints).
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TrapEntryOut, TrapToggleIn
from app.auth.deps import require_admin
from app.auth.models import User
from app.core.scoring.traps import ALL_TRAPS
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin/traps",
    tags=["admin-traps"],
    dependencies=[Depends(require_admin)],
)

GLOBAL_SCOPE: str = "*"


def _known_trap_meta() -> dict[str, tuple[str, str]]:
    """Map trap_id → (severity, side) for the registered universe."""
    return {t.trap_id: (t.severity, t.side) for t in ALL_TRAPS}


@router.get("", response_model=list[TrapEntryOut])
async def list_traps(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[TrapEntryOut]:
    """List all 17 traps with their current global enabled state.

    Cross-references any ``trap_enabled`` rows scoped to ``(*, *)`` so a
    disabled-everywhere row is reflected on the row. Per-symbol /
    per-timeframe disables are not surfaced here — those are intended for
    targeted admin actions and are best inspected by querying the table
    directly via the audit-trail endpoint.
    """
    rows = (await session.execute(sa.text(
        "SELECT trap_id, enabled, disabled_reason "
        "FROM trap_enabled WHERE symbol = :sym AND timeframe = :tf"
    ), {"sym": GLOBAL_SCOPE, "tf": GLOBAL_SCOPE})).all()
    by_id = {r.trap_id: r for r in rows}

    out: list[TrapEntryOut] = []
    for trap_id, (severity, side) in _known_trap_meta().items():
        row = by_id.get(trap_id)
        if row is None:
            out.append(TrapEntryOut(
                trap_id=trap_id,
                severity=severity,  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
            ))
        else:
            out.append(TrapEntryOut(
                trap_id=trap_id,
                severity=severity,  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
                enabled=bool(row.enabled),
                disabled_reason=row.disabled_reason,
            ))
    return out


def _resolve_scope(body: TrapToggleIn) -> tuple[str, str]:
    """``None``/missing fields fall back to the global ``*`` scope."""
    return (body.symbol or GLOBAL_SCOPE, body.timeframe or GLOBAL_SCOPE)


@router.post("/{trap_id}/disable", response_model=TrapEntryOut)
async def disable_trap(
    trap_id: str,
    body: TrapToggleIn,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TrapEntryOut:
    """Upsert a ``trap_enabled`` row with ``enabled=false``.

    Re-disabling an already-disabled trap updates the reason — useful when
    an admin wants to leave a fresh note without first re-enabling.
    """
    known = _known_trap_meta()
    if trap_id not in known:
        raise HTTPException(status_code=404, detail="unknown trap_id")

    symbol, timeframe = _resolve_scope(body)
    now = datetime.now(timezone.utc)

    existing = (await session.execute(sa.text(
        "SELECT id FROM trap_enabled "
        "WHERE trap_id = :p AND symbol = :s AND timeframe = :t"
    ), {"p": trap_id, "s": symbol, "t": timeframe})).first()

    if existing is None:
        await session.execute(sa.text(
            "INSERT INTO trap_enabled "
            "(trap_id, symbol, timeframe, enabled, disabled_reason, "
            " updated_at, updated_by) "
            "VALUES (:p, :s, :t, 0, :r, :u, :ub)"
        ), {
            "p": trap_id, "s": symbol, "t": timeframe,
            "r": body.reason, "u": now, "ub": current_admin.id,
        })
    else:
        await session.execute(sa.text(
            "UPDATE trap_enabled SET enabled = 0, disabled_reason = :r, "
            "updated_at = :u, updated_by = :ub WHERE id = :i"
        ), {
            "r": body.reason, "u": now, "ub": current_admin.id,
            "i": existing.id,
        })

    await session.commit()
    severity, side = known[trap_id]
    return TrapEntryOut(
        trap_id=trap_id,
        severity=severity,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        symbol=symbol, timeframe=timeframe,
        enabled=False, disabled_reason=body.reason,
    )


@router.post("/{trap_id}/enable", response_model=TrapEntryOut)
async def enable_trap(
    trap_id: str,
    body: TrapToggleIn | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TrapEntryOut:
    """Delete the disabled row(s) for ``trap_id`` at the requested scope.

    Idempotent — calling on an already-enabled trap returns 200 with the
    enabled row payload (no DB rows touched).
    """
    known = _known_trap_meta()
    if trap_id not in known:
        raise HTTPException(status_code=404, detail="unknown trap_id")

    body = body or TrapToggleIn()
    symbol, timeframe = _resolve_scope(body)

    await session.execute(sa.text(
        "DELETE FROM trap_enabled "
        "WHERE trap_id = :p AND symbol = :s AND timeframe = :t"
    ), {"p": trap_id, "s": symbol, "t": timeframe})
    await session.commit()

    severity, side = known[trap_id]
    return TrapEntryOut(
        trap_id=trap_id,
        severity=severity,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        symbol=symbol, timeframe=timeframe,
        enabled=True, disabled_reason=None,
    )
