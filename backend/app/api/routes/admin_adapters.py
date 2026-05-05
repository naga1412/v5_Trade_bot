"""Admin REST endpoints for data adapters (SP-3 Phase F).

All routes are gated by ``Depends(require_admin)`` from SP-0.7. The frontend
admin sub-page is deferred to SP-6; this dispatch ships only the backend
contract so other tooling (CLI, Postman) can drive it.

Endpoints:
- GET  /api/v1/admin/adapters/health           latest health row per exchange
- POST /api/v1/admin/adapters/{exchange}/sync  trigger universe sync
- GET  /api/v1/admin/universe                  list universe_history rows
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AdapterHealthOut,
    SyncResultOut,
    UniverseEntryOut,
)
from app.auth.deps import require_admin
from app.data.adapters import AdapterNotRegistered, get_adapter, list_registered
from app.data.universe_sync import sync_universe
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-adapters"],
    dependencies=[Depends(require_admin)],
)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@router.get("/adapters/health", response_model=list[AdapterHealthOut])
async def adapters_health(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AdapterHealthOut]:
    """Return latest health row per registered exchange.

    For exchanges with no ``adapter_health`` rows yet, returns a placeholder
    with ``is_healthy=False, error_message='no checks yet'``.
    """
    rows = (await session.execute(sa.text(
        "SELECT exchange, checked_at, is_healthy, latency_ms, "
        "error_message, quota_used_pct "
        "FROM adapter_health "
        "WHERE id IN ("
        "  SELECT MAX(id) FROM adapter_health GROUP BY exchange"
        ") "
        "ORDER BY exchange ASC"
    ))).all()
    seen: dict[str, AdapterHealthOut] = {}
    for r in rows:
        seen[r.exchange] = AdapterHealthOut(
            exchange=r.exchange,
            checked_at=_coerce_dt(r.checked_at),
            is_healthy=bool(r.is_healthy),
            latency_ms=r.latency_ms,
            error_message=r.error_message,
            quota_used_pct=r.quota_used_pct,
        )
    out: list[AdapterHealthOut] = []
    for ex in list_registered():
        if ex in seen:
            out.append(seen[ex])
        else:
            out.append(AdapterHealthOut(
                exchange=ex,
                checked_at=datetime.now(timezone.utc),
                is_healthy=False,
                error_message="no checks yet",
            ))
    return out


@router.post("/adapters/{exchange}/sync", response_model=SyncResultOut)
async def trigger_sync(
    exchange: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SyncResultOut:
    """Manually invoke ``sync_universe(adapter, session)`` for ``exchange``."""
    try:
        adapter = get_adapter(exchange)
    except AdapterNotRegistered as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown exchange: {exchange}",
        ) from exc

    result = await sync_universe(adapter, session)
    await session.commit()
    return SyncResultOut(
        exchange=adapter.name,
        added=result.added,
        still_active=result.still_active,
        newly_delisted=result.newly_delisted,
    )


@router.get("/universe", response_model=list[UniverseEntryOut])
async def list_universe(
    exchange: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[UniverseEntryOut]:
    """List ``universe_history`` rows with optional exchange / active filters."""
    where: list[str] = []
    params: dict[str, Any] = {"lim": limit, "off": offset}
    if exchange is not None:
        where.append("exchange = :ex")
        params["ex"] = exchange
    if active is True:
        where.append("delisted_at IS NULL")
    elif active is False:
        where.append("delisted_at IS NOT NULL")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(sa.text(
        "SELECT exchange, symbol, asset_class, listed_at, delisted_at, "
        "last_synced_at FROM universe_history" + where_sql + " "
        "ORDER BY exchange, symbol LIMIT :lim OFFSET :off"
    ), params)).all()
    return [
        UniverseEntryOut(
            exchange=r.exchange,
            symbol=r.symbol,
            asset_class=r.asset_class,
            listed_at=_coerce_dt(r.listed_at),
            delisted_at=_coerce_dt(r.delisted_at) if r.delisted_at else None,
            last_synced_at=_coerce_dt(r.last_synced_at),
        )
        for r in rows
    ]
