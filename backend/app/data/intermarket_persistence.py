"""SP-3.5 Phase B5/B6: intermarket_snapshots persistence + cleanup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.adapters.binance_futures_intermarket import IntermarketSnapshot


log = logging.getLogger(__name__)


async def persist_intermarket_snapshots(
    session: AsyncSession,
    snapshots: Iterable[IntermarketSnapshot],
) -> int:
    """INSERT each snapshot into ``intermarket_snapshots``. Returns # inserted."""
    snaps = list(snapshots)
    if not snaps:
        return 0
    sql = sa.text("""
        INSERT INTO intermarket_snapshots
          (symbol, captured_at, funding_rate, mark_price,
           open_interest, source)
        VALUES
          (:symbol, :captured_at, :funding_rate, :mark_price,
           :open_interest, :source)
    """)
    inserted = 0
    for s in snaps:
        await session.execute(sql, {
            "symbol": s.symbol,
            "captured_at": s.captured_at,
            "funding_rate": s.funding_rate,
            "mark_price": s.mark_price,
            "open_interest": s.open_interest,
            "source": s.source,
        })
        inserted += 1
    await session.commit()
    return inserted


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
