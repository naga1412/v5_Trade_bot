"""SP-PAUSE master pause/resume state.

Single Redis key (``system:paused`` = ``"true"`` / ``"false"``) cached in
process for 1s so worker ticks + every HTTP request don't hit Redis on
every call. State changes go through ``set_paused`` which both updates
Redis and inserts a row into ``auth_violations`` (the SP-0.7 audit
table) so we have an append-only log of who paused/resumed and why.

The 1-second cache means a freshly-paused-from-another-process flip
takes <=1s to propagate to this process - acceptable per spec section 3.1
(workers tick every 5min; HTTP routes don't need sub-second precision).

Public surface:

* :func:`is_paused` - fast (cached) bool getter.
* :func:`set_paused` - toggle + audit-row insert. Spec section 3.2.
* :func:`get_state` - returns :class:`SystemPauseState` for /admin/system/state.
* :func:`pause_event_log` - last N audit rows from ``auth_violations``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import redis.asyncio as aioredis

from app.config import get_settings


log = logging.getLogger(__name__)

REDIS_KEY: str = "system:paused"
SINCE_KEY: str = "system:paused_since"
BY_KEY: str = "system:paused_by"
REASON_KEY: str = "system:paused_reason"
CACHE_TTL_S: float = 1.0

_REDIS: aioredis.Redis | None = None
_CACHE: tuple[bool, float] | None = None


@dataclass(frozen=True)
class SystemPauseState:
    paused: bool
    since: datetime | None
    by_email: str | None
    reason: str | None


PauseEventKind = Literal["system_paused", "system_resumed"]


@dataclass(frozen=True)
class PauseEvent:
    id: int
    kind: PauseEventKind
    by_email: str
    at: datetime
    reason: str | None


def _get_redis() -> aioredis.Redis:
    global _REDIS
    if _REDIS is None:
        _REDIS = aioredis.from_url(
            get_settings().redis_url, decode_responses=True,
        )
    return _REDIS


def _reset_for_tests() -> None:
    """Test hook: clear the cached Redis client + in-process cache."""
    global _REDIS, _CACHE
    _REDIS = None
    _CACHE = None


async def is_paused() -> bool:
    """Cached pause-flag getter. Returns the in-process cached value when
    less than ``CACHE_TTL_S`` seconds old; otherwise refreshes from Redis."""
    global _CACHE
    now = time.monotonic()
    if _CACHE is not None and now - _CACHE[1] < CACHE_TTL_S:
        return _CACHE[0]
    try:
        raw = await _get_redis().get(REDIS_KEY)
    except Exception:  # noqa: BLE001
        # If Redis is unreachable, fail OPEN (not paused) - avoids a
        # Redis outage cascading into a full system lockout.
        log.warning("pause_state: Redis read failed; assuming not-paused")
        _CACHE = (False, now)
        return False
    value = raw == "true"
    _CACHE = (value, now)
    return value


async def set_paused(  # type: ignore[no-untyped-def]
    paused: bool, *, by_email: str, reason: str | None,
    session, request_path: str | None = None,
) -> None:
    """Flip the pause flag in Redis AND record an audit row.

    The audit row uses ``auth_violations`` (the SP-0.7 table). Reason
    field encodes the kind so :func:`pause_event_log` can recover it::

        pause:    reason = "system_paused: <free text or empty>"
        resume:   reason = "system_resumed"
    """
    global _CACHE
    import sqlalchemy as sa  # local import - keeps redis-only callers light
    from datetime import timezone

    r = _get_redis()
    if paused:
        ts = datetime.now(timezone.utc).isoformat()
        await r.set(REDIS_KEY, "true")
        await r.set(SINCE_KEY, ts)
        await r.set(BY_KEY, by_email)
        await r.set(REASON_KEY, reason or "")
        audit_reason = f"system_paused: {reason or ''}"
    else:
        await r.delete(REDIS_KEY, SINCE_KEY, BY_KEY, REASON_KEY)
        audit_reason = "system_resumed"

    await session.execute(
        sa.text(
            "INSERT INTO auth_violations "
            "(attempted_email, reason, request_path) "
            "VALUES (:e, :r, :p)"
        ),
        {"e": by_email, "r": audit_reason, "p": request_path},
    )
    await session.commit()
    _CACHE = None


async def get_state() -> SystemPauseState:
    r = _get_redis()
    paused_raw, since_raw, by_raw, reason_raw = await r.mget(
        REDIS_KEY, SINCE_KEY, BY_KEY, REASON_KEY,
    )
    paused = paused_raw == "true"
    if not paused:
        return SystemPauseState(
            paused=False, since=None, by_email=None, reason=None,
        )
    since: datetime | None = None
    if since_raw:
        try:
            since = datetime.fromisoformat(since_raw)
        except ValueError:
            since = None
    return SystemPauseState(
        paused=True,
        since=since,
        by_email=by_raw or None,
        reason=(reason_raw or None) or None,
    )


async def pause_event_log(  # type: ignore[no-untyped-def]
    session, *, limit: int = 50,
) -> list[PauseEvent]:
    import sqlalchemy as sa
    rows = (await session.execute(sa.text("""
        SELECT id, attempted_email, attempted_at, reason
        FROM auth_violations
        WHERE reason LIKE 'system_paused%' OR reason = 'system_resumed'
        ORDER BY id DESC
        LIMIT :limit
    """), {"limit": limit})).all()
    out: list[PauseEvent] = []
    for r in rows:
        kind: PauseEventKind
        if r.reason == "system_resumed":
            kind = "system_resumed"
            msg: str | None = None
        else:
            kind = "system_paused"
            # Strip "system_paused: " prefix; empty -> None.
            after = r.reason[len("system_paused:"):].strip()
            msg = after if after else None
        at = r.attempted_at
        if isinstance(at, str):
            try:
                at = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError:
                pass
        out.append(PauseEvent(
            id=int(r.id), kind=kind, by_email=r.attempted_email,
            at=at, reason=msg,
        ))
    return out


__all__ = [
    "BY_KEY",
    "CACHE_TTL_S",
    "PauseEvent",
    "REASON_KEY",
    "REDIS_KEY",
    "SINCE_KEY",
    "SystemPauseState",
    "_reset_for_tests",
    "get_state",
    "is_paused",
    "pause_event_log",
    "set_paused",
]
