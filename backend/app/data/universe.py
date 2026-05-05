"""Point-in-time universe (§5.2) — SP-3 DB-backed implementation.

Replaces the SP-0 hardcoded shortcut. ``is_tradable(session, symbol, ts)``
queries ``universe_history`` and returns True iff ANY exchange has a row
where ``listed_at <= ts AND (delisted_at IS NULL OR ts < delisted_at)``.

The function is async + takes an AsyncSession because the universe table is
a moving target (daily syncs); we query at call time, not at import.
"""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

BTC_USDT: str = "BTC/USDT"


async def is_tradable(
    session: AsyncSession, symbol: str, ts: datetime,
) -> bool:
    """Return True if ``symbol`` was tradable on at least one exchange at ``ts``.

    Spec §2 decision #10: "Returns ``True`` only if a ``universe_history`` row
    exists with ``listed_at <= ts < (delisted_at OR +infinity)`` for ANY
    exchange."
    """
    row = (await session.execute(
        sa.text(
            "SELECT 1 FROM universe_history "
            "WHERE symbol = :s "
            "  AND listed_at <= :ts "
            "  AND (delisted_at IS NULL OR :ts < delisted_at) "
            "LIMIT 1"
        ),
        {"s": symbol, "ts": ts},
    )).first()
    return row is not None
