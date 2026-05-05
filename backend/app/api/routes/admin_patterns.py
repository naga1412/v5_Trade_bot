"""Admin REST endpoints for pattern enable/disable (SP-2 Phase E task E5).

All routes are gated by ``Depends(require_admin)`` from SP-0.7. The frontend
admin sub-page is deferred to SP-6; this dispatch ships only the backend
contract so other tooling (CLI, Postman) can already drive it.

Schema:
- ``GET /api/v1/admin/patterns`` returns one row per registered pattern,
  joined against any ``pattern_enabled`` row at scope ``(*, *)`` so the
  global default is reflected. Patterns without a row are reported as
  enabled.
- ``POST /api/v1/admin/patterns/{pattern_id}/disable`` upserts a
  ``pattern_enabled`` row with ``enabled=false`` (optionally scoped by symbol
  and/or timeframe — defaulting to the global ``*`` scope).
- ``POST /api/v1/admin/patterns/{pattern_id}/enable`` removes the matching
  ``pattern_enabled`` row(s) so the pattern returns to its default-enabled
  state. Idempotent — never 404s on already-enabled patterns.

After every mutation the in-process pattern_stats cache is left untouched
(disabled patterns are filtered by ``enabled_patterns`` set, not by stats),
but a future task may also flush an ``enabled_patterns`` cache here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PatternEntryOut, PatternToggleIn
from app.auth.deps import require_admin
from app.auth.models import User
from app.core.patterns import ALL_PATTERNS
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin/patterns",
    tags=["admin-patterns"],
    dependencies=[Depends(require_admin)],
)

GLOBAL_SCOPE: str = "*"


def _known_pattern_ids() -> dict[str, str]:
    """Map pattern_id → pattern_type for the registered universe."""
    return {p.pattern_id: p.pattern_type for p in ALL_PATTERNS}


@router.get("", response_model=list[PatternEntryOut])
async def list_patterns(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PatternEntryOut]:
    """List all registered patterns with their current global enabled state.

    Cross-references any ``pattern_enabled`` rows scoped to ``(*, *)`` so a
    disabled-everywhere row is reflected on the row. Per-symbol /
    per-timeframe disables are not surfaced here — those are intended for
    targeted admin actions and are best inspected by querying the table
    directly via the audit-trail endpoint.
    """
    rows = (await session.execute(sa.text(
        "SELECT pattern_id, enabled, disabled_reason "
        "FROM pattern_enabled WHERE symbol = :sym AND timeframe = :tf"
    ), {"sym": GLOBAL_SCOPE, "tf": GLOBAL_SCOPE})).all()
    by_id = {r.pattern_id: r for r in rows}

    out: list[PatternEntryOut] = []
    for pattern_id, pattern_type in _known_pattern_ids().items():
        row = by_id.get(pattern_id)
        if row is None:
            out.append(PatternEntryOut(
                pattern_id=pattern_id, pattern_type=pattern_type,  # type: ignore[arg-type]
            ))
        else:
            out.append(PatternEntryOut(
                pattern_id=pattern_id,
                pattern_type=pattern_type,  # type: ignore[arg-type]
                enabled=bool(row.enabled),
                disabled_reason=row.disabled_reason,
            ))
    return out


def _resolve_scope(body: PatternToggleIn) -> tuple[str, str]:
    """``None``/missing fields fall back to the global ``*`` scope."""
    return (body.symbol or GLOBAL_SCOPE, body.timeframe or GLOBAL_SCOPE)


@router.post("/{pattern_id}/disable", response_model=PatternEntryOut)
async def disable_pattern(
    pattern_id: str,
    body: PatternToggleIn,
    current_admin: User = Depends(require_admin),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PatternEntryOut:
    """Upsert a ``pattern_enabled`` row with ``enabled=false``.

    Re-disabling an already-disabled pattern updates the reason — useful
    when an admin wants to leave a fresh note.
    """
    known = _known_pattern_ids()
    if pattern_id not in known:
        raise HTTPException(status_code=404, detail="unknown pattern_id")

    symbol, timeframe = _resolve_scope(body)
    now = datetime.now(timezone.utc)

    existing = (await session.execute(sa.text(
        "SELECT id FROM pattern_enabled "
        "WHERE pattern_id = :p AND symbol = :s AND timeframe = :t"
    ), {"p": pattern_id, "s": symbol, "t": timeframe})).first()

    if existing is None:
        await session.execute(sa.text(
            "INSERT INTO pattern_enabled "
            "(pattern_id, symbol, timeframe, enabled, disabled_reason, "
            " updated_at, updated_by) "
            "VALUES (:p, :s, :t, 0, :r, :u, :ub)"
        ), {
            "p": pattern_id, "s": symbol, "t": timeframe,
            "r": body.reason, "u": now, "ub": current_admin.id,
        })
    else:
        await session.execute(sa.text(
            "UPDATE pattern_enabled SET enabled = 0, disabled_reason = :r, "
            "updated_at = :u, updated_by = :ub WHERE id = :i"
        ), {
            "r": body.reason, "u": now, "ub": current_admin.id,
            "i": existing.id,
        })

    await session.commit()

    return PatternEntryOut(
        pattern_id=pattern_id,
        pattern_type=known[pattern_id],  # type: ignore[arg-type]
        symbol=symbol, timeframe=timeframe,
        enabled=False, disabled_reason=body.reason,
    )


@router.post("/{pattern_id}/enable", response_model=PatternEntryOut)
async def enable_pattern(
    pattern_id: str,
    body: PatternToggleIn | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PatternEntryOut:
    """Delete the disabled row(s) for ``pattern_id`` at the requested scope.

    Idempotent — calling on an already-enabled pattern returns 200 with the
    enabled row payload (no DB rows touched).
    """
    known = _known_pattern_ids()
    if pattern_id not in known:
        raise HTTPException(status_code=404, detail="unknown pattern_id")

    body = body or PatternToggleIn()
    symbol, timeframe = _resolve_scope(body)

    await session.execute(sa.text(
        "DELETE FROM pattern_enabled "
        "WHERE pattern_id = :p AND symbol = :s AND timeframe = :t"
    ), {"p": pattern_id, "s": symbol, "t": timeframe})
    await session.commit()

    return PatternEntryOut(
        pattern_id=pattern_id,
        pattern_type=known[pattern_id],  # type: ignore[arg-type]
        symbol=symbol, timeframe=timeframe,
        enabled=True, disabled_reason=None,
    )
