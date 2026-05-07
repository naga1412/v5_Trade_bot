"""Adapter health pinger (SP-3 Phase F).

Every ``DEFAULT_HEALTH_INTERVAL_SEC`` seconds, walk every registered adapter
and:

1. Call its ``health_check()`` if it implements the ``HealthProbe`` protocol.
2. Fall back to a placeholder result (healthy + note) if it does not.
3. Catch any exception and record an unhealthy row.
4. INSERT a row into ``adapter_health``.

The admin route ``GET /api/v1/admin/adapters/health`` reads the latest row
per exchange — see ``app/api/routes/admin_adapters.py``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters import get_adapter, list_registered

log = logging.getLogger(__name__)

# 5 minutes between probes — frequent enough to catch outages quickly without
# burning rate-limit quota on the upstream APIs.
DEFAULT_HEALTH_INTERVAL_SEC: int = 300


@dataclass(frozen=True)
class HealthResult:
    """Result of a single ``health_check()`` call on an adapter."""

    is_healthy: bool
    latency_ms: int | None = None
    error_message: str | None = None
    quota_used_pct: float | None = None


@runtime_checkable
class HealthProbe(Protocol):
    """Adapters opt in to health probing by exposing this method."""

    async def health_check(self) -> HealthResult: ...


async def probe_adapter(adapter: Any) -> HealthResult:
    """Invoke ``adapter.health_check()`` if defined; classify the result.

    - Adapter raises → unhealthy with ``error_message=str(exc)``.
    - Adapter has no ``health_check`` → healthy with explanatory note.
    """
    if not isinstance(adapter, HealthProbe):
        return HealthResult(
            is_healthy=True,
            error_message=f"adapter {getattr(adapter, 'name', '?')} "
            f"has no health_check",
        )
    try:
        return await adapter.health_check()
    except Exception as exc:  # noqa: BLE001
        return HealthResult(is_healthy=False, error_message=str(exc))


async def record_health(
    session: AsyncSession,
    *,
    exchange: str,
    result: HealthResult,
    checked_at: datetime | None = None,
) -> None:
    """INSERT a single row into ``adapter_health``."""
    # Pass datetime directly; asyncpg/PostgreSQL TIMESTAMPTZ rejects ISO
    # strings, SQLite tests still get TEXT via SQLAlchemy. SP-1.1 hotfix.
    ts = checked_at or datetime.now(UTC)
    await session.execute(
        sa.text(
            "INSERT INTO adapter_health "
            "(exchange, checked_at, is_healthy, latency_ms, "
            "error_message, quota_used_pct) "
            "VALUES (:ex, :ts, :h, :lat, :err, :q)"
        ),
        {
            "ex": exchange,
            "ts": ts,
            "h": 1 if result.is_healthy else 0,
            "lat": result.latency_ms,
            "err": result.error_message,
            "q": result.quota_used_pct,
        },
    )


async def run_health_pinger_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    interval_sec: int = DEFAULT_HEALTH_INTERVAL_SEC,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Forever-loop: every ``interval_sec`` ping each registered adapter."""
    while True:
        await _sleep(float(interval_sec))
        for ex in list_registered():
            try:
                adapter = get_adapter(ex)
                result = await probe_adapter(adapter)
                async with session_factory() as session:
                    await record_health(
                        session, exchange=ex, result=result,
                    )
                    await session.commit()
                log.info(
                    "adapter_health(%s): healthy=%s latency_ms=%s",
                    ex, result.is_healthy, result.latency_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("adapter_health(%s) failed: %s", ex, e)


def start_health_pinger_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_sec: int = DEFAULT_HEALTH_INTERVAL_SEC,
) -> asyncio.Task[None]:
    """Spawn ``run_health_pinger_loop`` as a background task."""
    return asyncio.create_task(run_health_pinger_loop(
        session_factory=session_factory,
        interval_sec=interval_sec,
    ))


__all__ = [
    "DEFAULT_HEALTH_INTERVAL_SEC",
    "HealthProbe",
    "HealthResult",
    "probe_adapter",
    "record_health",
    "run_health_pinger_loop",
    "start_health_pinger_task",
]
