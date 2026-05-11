"""Worker heartbeat helper.

Each background worker calls ``record_heartbeat(name)`` from its main loop
so the watchdog (``app.ops.worker_watchdog``) can detect a silent failure.
The watchdog reads ``MAX(beat_at)`` per worker and alerts when stale.

Heartbeats are best-effort — a heartbeat failure must NEVER kill the
calling worker. We swallow + log on error.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)


async def record_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    worker_name: str,
    *,
    status: str = "ok",
    details: dict[str, Any] | None = None,
) -> None:
    """Upsert a heartbeat row for ``worker_name``. Never raises.

    Use a fresh short-lived session so we don't hold a connection while
    the worker does its real work.
    """
    payload = json.dumps(details) if details is not None else None
    try:
        async with session_factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO worker_heartbeats "
                    "(worker_name, beat_at, last_status, details) "
                    "VALUES (:n, NOW(), :s, CAST(:d AS JSONB)) "
                    "ON CONFLICT (worker_name) DO UPDATE SET "
                    "beat_at = NOW(), last_status = :s, details = CAST(:d AS JSONB)"
                ),
                {"n": worker_name, "s": status, "d": payload},
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 — best-effort, must never kill the worker
        log.warning("record_heartbeat(%s) failed: %s", worker_name, e)
