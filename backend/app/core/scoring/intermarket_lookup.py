"""Read-only latest funding rate lookup for the aggregator hook (PR1)."""
from __future__ import annotations

import logging
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def lookup_latest_funding_rate(
    session: AsyncSession,
    symbol: str,
) -> float | None:
    """Return the most-recent funding_rate from intermarket_snapshots, or None.

    Fail-open: any DB error logged + returns None.
    Returns None when no rows exist for the symbol.

    Note: uses ``captured_at`` (not ``ts``) — the real schema column name
    per alembic/versions/2026_05_06_0014_intermarket.py.
    """
    try:
        result = await session.execute(
            sa.text(
                "SELECT funding_rate FROM intermarket_snapshots "
                "WHERE symbol = :symbol "
                "ORDER BY captured_at DESC LIMIT 1"
            ),
            {"symbol": symbol},
        )
        row = result.first()
        if row is None or row.funding_rate is None:
            return None
        return float(row.funding_rate)
    except Exception as exc:  # noqa: BLE001 — fail-open per spec
        log.warning("lookup_latest_funding_rate failed for %s: %s", symbol, exc)
        return None


__all__ = ["lookup_latest_funding_rate"]
