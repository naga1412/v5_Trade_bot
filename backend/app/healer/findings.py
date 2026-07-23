"""Writers into ``healer_findings`` + ``healer_known_error_types``.

Callers:
  * detectors — INSERT into healer_findings on detection.
  * dispatcher — INSERT into healer_findings on caught exception via
    :func:`record_dispatch_error`, so C1 (dispatch-outcome monitor) can
    aggregate exception classes and alarm on novel types / >5/hr rate.

Best-effort semantics: a write failure is logged + swallowed. The
healer must never take a real code path down.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


log = logging.getLogger(__name__)


_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})

# Dedicated detector name for dispatcher exceptions. C1 filters on this
# string so a renamed detector doesn't silently break the aggregation.
DISPATCH_EXCEPTION_DETECTOR: str = "dispatcher_exception"


@dataclass(frozen=True)
class HealerFinding:
    """A single detection event, as stored in ``healer_findings``."""

    detector_name: str
    severity: str
    summary: str
    details: dict[str, Any] | None = None


async def record_finding(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    detector_name: str,
    severity: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> None:
    """INSERT one row into ``healer_findings``. Never raises."""
    if severity not in _ALLOWED_SEVERITIES:
        log.warning(
            "healer: rejecting finding with unknown severity %r (name=%s)",
            severity, detector_name,
        )
        return
    payload = json.dumps(details) if details is not None else None
    try:
        async with session_factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO healer_findings "
                    "(detector_name, severity, summary, details) "
                    "VALUES (:d, :s, :m, CAST(:p AS JSONB))"
                ),
                {"d": detector_name, "s": severity, "m": summary, "p": payload},
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 — best-effort
        log.warning("healer: record_finding failed (%s): %s", detector_name, e)


async def record_dispatch_error(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    exception: BaseException,
    context: dict[str, Any] | None = None,
) -> None:
    """Record a dispatcher-level exception for C1 aggregation.

    Dispatcher's outermost try/except should call this with the caught
    exception. C1's detector (see :mod:`app.healer.detectors`) rolls up
    ``details.exception_type`` per hour and cross-references against
    ``healer_known_error_types``.
    """
    exc_type = type(exception).__name__
    message = str(exception)
    details: dict[str, Any] = {
        "exception_type": exc_type,
        "message": message[:500],
    }
    if context:
        details["context"] = context
    await record_finding(
        session_factory,
        detector_name=DISPATCH_EXCEPTION_DETECTOR,
        severity="warning",
        summary=f"dispatch exception: {exc_type}",
        details=details,
    )


async def upsert_known_error_type(
    session: AsyncSession, error_type: str,
) -> bool:
    """UPSERT into ``healer_known_error_types``.

    Returns True iff this was the FIRST time we've seen ``error_type``
    (i.e. C1 should alarm as novel). False if already known.
    """
    from datetime import datetime, timezone
    row = (
        await session.execute(
            sa.text(
                "SELECT 1 FROM healer_known_error_types "
                "WHERE error_type = :e"
            ),
            {"e": error_type},
        )
    ).first()
    is_novel = row is None
    # Bind `now` as a raw datetime rather than embedding NOW() in SQL —
    # NOW() is Postgres-only; SQLite needs CURRENT_TIMESTAMP. Parameter
    # binding keeps this portable and dodges the isoformat SQL bind bug
    # class in one move.
    now = datetime.now(tz=timezone.utc)
    await session.execute(
        sa.text(
            "INSERT INTO healer_known_error_types "
            "(error_type, first_seen_at, last_seen_at, seen_count) "
            "VALUES (:e, :now, :now, 1) "
            "ON CONFLICT (error_type) DO UPDATE SET "
            "last_seen_at = :now, "
            "seen_count = healer_known_error_types.seen_count + 1"
        ),
        {"e": error_type, "now": now},
    )
    return is_novel


__all__ = [
    "DISPATCH_EXCEPTION_DETECTOR",
    "HealerFinding",
    "record_dispatch_error",
    "record_finding",
    "upsert_known_error_type",
]
