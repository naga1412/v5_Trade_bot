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
from typing import Any

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


@dataclass(frozen=True)
class PauseEvent:
    id: int
    kind: str          # "system_paused" | "system_resumed"
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


async def is_paused() -> bool:  # noqa: D401
    raise NotImplementedError("SP-PAUSE Phase A2")


async def set_paused(  # type: ignore[no-untyped-def]
    paused: bool, *, by_email: str, reason: str | None, session, request_path: str | None = None,
) -> None:
    raise NotImplementedError("SP-PAUSE Phase A2")


async def get_state() -> SystemPauseState:
    raise NotImplementedError("SP-PAUSE Phase A2")


async def pause_event_log(  # type: ignore[no-untyped-def]
    session, *, limit: int = 50,
) -> list[PauseEvent]:
    raise NotImplementedError("SP-PAUSE Phase B2")


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
