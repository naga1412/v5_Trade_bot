"""Telegram dispatch dedup gate — 2026-09-04.

Reads `telegram_signals` for the most recent `sent_at` on
`(symbol, direction)` and suppresses the CURRENT card if it falls
inside `TELEGRAM_DEDUP_COOLDOWN_HOURS` of that prior send. `None`
(the default) disables the gate entirely — every call returns None
(don't suppress), byte-identical to today's behavior.

**Scope, non-negotiable**: this gate sits ONLY at the Telegram-approve
send call site inside `dispatcher.dispatch()`. It does not touch signal
generation, shadow trade creation, or the breakeven-variant lanes — none
of those call this function or read `telegram_signals` for their own
gating. A suppressed card is still a real, fully-evaluated signal for
every other purpose in the system.

**Fail-open contract** (matches `cooldown_gate.py`'s established
philosophy exactly): any error from the DB read path is logged (warn)
and returns None (let the send proceed). A dedup gate that erred to
suppress would silently drop real signals on a DB blip — worse than
sending an occasional duplicate.

**Keyed on (symbol, direction), never symbol alone**: a direction
reversal is new information about the setup and must always send,
independent of how recently the OPPOSITE direction last sent for that
symbol.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _check_telegram_dedup(
    *,
    session: AsyncSession,
    symbol: str,
    direction: str,
    cooldown_hours: float | None,
    now_fn: Callable[[], datetime] = _utc_now,
) -> str | None:
    """Return a suppression reason string to block; None to let the send proceed.

    None on: gate disabled (`cooldown_hours` is None), no prior send for
    this (symbol, direction), the prior send is outside the cooldown
    window, OR any DB error (fail-open).

    Imported lazily inside `dispatcher.py`'s telegram-approve branch,
    matching `_apply_cooldown_gate`'s own lazy-import convention (avoids
    a module-level import cost on the fully-auto / manual paths that
    never reach this check).
    """
    if cooldown_hours is None:
        return None

    try:
        row = (
            await session.execute(
                sa.text(
                    "SELECT MAX(sent_at) AS last_sent_at FROM telegram_signals "
                    "WHERE symbol = :s AND direction = :d"
                ),
                {"s": symbol, "d": direction},
            )
        ).one_or_none()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "telegram_dedup_gate DB read failed for symbol=%s direction=%s; "
            "failing open: %s",
            symbol, direction, e,
        )
        return None

    if row is None or row.last_sent_at is None:
        return None

    last_sent_at = row.last_sent_at
    if last_sent_at.tzinfo is None:
        last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)

    elapsed = now_fn() - last_sent_at
    if elapsed >= timedelta(hours=cooldown_hours):
        return None

    remaining = timedelta(hours=cooldown_hours) - elapsed
    return (
        f"telegram_dedup: {symbol} {direction} sent {elapsed} ago, "
        f"cooldown={cooldown_hours}h, {remaining} remaining"
    )
