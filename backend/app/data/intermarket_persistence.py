"""SP-3.5 intermarket persistence layer.

All four functions are scaffolded in Phase A4 and implemented in Phase B5/B6.
They share the :class:`IntermarketSnapshot` dataclass with the adapter so
the row → snapshot conversion has one canonical shape.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot


async def persist_intermarket_snapshots(
    session: AsyncSession,
    snapshots: Iterable[IntermarketSnapshot],
) -> int:
    raise NotImplementedError("SP-3.5 Phase B5")


async def cleanup_old_intermarket(
    session: AsyncSession, *, older_than_days: int = 14,
) -> int:
    raise NotImplementedError("SP-3.5 Phase B6")


async def latest_snapshot_for(
    session: AsyncSession, symbol: str,
) -> IntermarketSnapshot | None:
    raise NotImplementedError("SP-3.5 Phase B6")


async def snapshot_at_or_before(
    session: AsyncSession, symbol: str, *, ts: datetime,
) -> IntermarketSnapshot | None:
    raise NotImplementedError("SP-3.5 Phase B6")
