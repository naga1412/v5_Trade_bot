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


async def latest_snapshot_for(
    session: AsyncSession, symbol: str,
) -> IntermarketSnapshot | None:
    sql = sa.text("""
        SELECT symbol, captured_at, funding_rate, mark_price,
               open_interest, source
        FROM intermarket_snapshots
        WHERE symbol = :symbol
        ORDER BY captured_at DESC
        LIMIT 1
    """)
    row = (await session.execute(sql, {"symbol": symbol})).first()
    if row is None:
        return None
    return _row_to_snapshot(row)


async def snapshot_at_or_before(
    session: AsyncSession, symbol: str, *, ts: datetime,
) -> IntermarketSnapshot | None:
    sql = sa.text("""
        SELECT symbol, captured_at, funding_rate, mark_price,
               open_interest, source
        FROM intermarket_snapshots
        WHERE symbol = :symbol AND captured_at <= :ts
        ORDER BY captured_at DESC
        LIMIT 1
    """)
    row = (await session.execute(sql, {"symbol": symbol, "ts": ts})).first()
    if row is None:
        return None
    return _row_to_snapshot(row)


async def cleanup_old_intermarket(
    session: AsyncSession, *, older_than_days: int = 14,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        sa.text("DELETE FROM intermarket_snapshots WHERE captured_at < :cutoff"),
        {"cutoff": cutoff},
    )
    await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    log.info(
        "cleanup_old_intermarket: deleted %d rows older than %dd",
        deleted, older_than_days,
    )
    return deleted


def _row_to_snapshot(row) -> IntermarketSnapshot:  # type: ignore[no-untyped-def]
    captured_at = row.captured_at
    if isinstance(captured_at, str):
        # SQLite returns TEXT — parse to UTC datetime.
        captured_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
    return IntermarketSnapshot(
        symbol=row.symbol,
        captured_at=captured_at,
        funding_rate=row.funding_rate,
        mark_price=row.mark_price,
        open_interest=row.open_interest,
        source=row.source,
    )
